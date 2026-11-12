"""
lib/rede.py - Wi-Fi da placa: conecta, reconecta sozinho e guarda a senha.

As credenciais ficam no arquivo config.py da própria placa. Ele pode ser
criado à mão (a partir do config.py.example) ou pelo app do computador,
que manda a rede e a senha pelo cabo USB.

A conexão roda numa tarefa do asyncio: enquanto o Wi-Fi conecta (alguns
segundos), o display, a matriz e o sensor continuam funcionando.
"""

import sys
import time

import network
import rp2

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

ARQUIVO_CONFIG = "config.py"
PAIS = "BR"                        # define os canais de rádio permitidos no Brasil
FUSO_PADRAO = -3                   # Brasília (usado pelo NTP numa etapa futura)

TEMPO_LIMITE_CONEXAO_MS = 20000    # desiste da tentativa depois de 20 s
# Depois de uma falha, as primeiras novas tentativas saem rápido e depois
# espaçam até 30 s. Logo após ligar a placa, a 1ª tentativa costuma falhar
# (o chip de Wi-Fi ainda está acordando) e a 2ª conecta em poucos segundos.
ESPERAS_NOVA_TENTATIVA_MS = (2000, 5000, 10000, 30000)
INTERVALO_VIGIA_MS = 2000          # de quanto em quanto tempo confere se a conexão caiu
INTERVALO_STATUS_MS = 250          # durante a conexão, confere o status 4x por segundo
CONFIRMA_FALHA_MS = 2000           # um erro só vale se continuar por 2 s seguidos

# Situações possíveis da conexão. Os textos são curtos e sem acento porque
# aparecem no OLED; o app do PC traduz para frases completas.
SEM_CONFIG, CONECTANDO, CONECTADO, SENHA_ERRADA, REDE_AUSENTE, FALHOU = range(6)
NOMES_ESTADO = ("SEM CONFIG", "CONECTANDO", "CONECTADO",
                "SENHA ERRADA", "REDE AUSENTE", "FALHOU")
# Dica mostrada no rodapé da tela WI-FI (máximo de 15 caracteres)
DICAS_ESTADO = ("CONFIGURE NO PC", "AGUARDE...", "",
                "CONFIRA A SENHA", "REDE FORA DO AR", "NOVA TENTATIVA")


def ler_config():
    """Lê o config.py da placa e devolve (ssid, senha, fuso).

    Remover o módulo de sys.modules obriga o import a ler o arquivo de
    novo; sem isso o Python devolveria a versão antiga guardada na memória.
    """
    sys.modules.pop("config", None)
    try:
        import config
    except ImportError:
        return None, None, FUSO_PADRAO
    except SyntaxError:
        print("[AVISO] config.py com erro de digitação; ignorando.")
        return None, None, FUSO_PADRAO
    return (getattr(config, "WIFI_SSID", None) or None,
            getattr(config, "WIFI_SENHA", ""),
            getattr(config, "FUSO_HORARIO", FUSO_PADRAO))


def salvar_config(ssid, senha, fuso):
    # repr() coloca as aspas e "escapa" caracteres especiais da senha,
    # para o arquivo continuar sendo Python válido.
    with open(ARQUIVO_CONFIG, "w") as arquivo:
        arquivo.write("# Criado pela placa a partir do app do computador.\n")
        arquivo.write("# Contém a senha do Wi-Fi: nunca envie este arquivo para o Git.\n\n")
        arquivo.write("WIFI_SSID = %s\n" % repr(ssid))
        arquivo.write("WIFI_SENHA = %s\n" % repr(senha))
        arquivo.write("FUSO_HORARIO = %s\n" % repr(fuso))


class Rede:
    def __init__(self, ao_mudar=None):
        rp2.country(PAIS)
        self.wlan = network.WLAN(network.STA_IF)
        self.ao_mudar = ao_mudar   # função chamada quando o estado muda
        self.estado = SEM_CONFIG
        self.ssid = None
        self.ip = None
        self.rssi = None           # força do sinal em dBm (-30 ótimo, -90 péssimo)
        self._pedido = asyncio.Event()

    # -----------------------------------------------------------------------
    # API usada pelo main.py e pelo display
    # -----------------------------------------------------------------------
    @property
    def conectado(self):
        return self.estado == CONECTADO

    @property
    def nome_estado(self):
        return NOMES_ESTADO[self.estado]

    @property
    def dica(self):
        return DICAS_ESTADO[self.estado]

    def configurar(self, ssid, senha):
        """Grava a nova rede no config.py e pede uma reconexão."""
        _, _, fuso = ler_config()
        salvar_config(ssid, senha, fuso)
        self._pedido.set()

    def procurar_redes(self, maximo=12):
        """Lista as redes visíveis, da mais forte para a mais fraca.

        Atenção: wlan.scan() trava o programa por 2 a 3 segundos. Por isso
        só é chamado quando alguém pede, nunca num laço automático.
        """
        self.wlan.active(True)
        vistas = {}
        for ssid, _bssid, _canal, rssi, seguranca, _oculta in self.wlan.scan():
            try:
                nome = ssid.decode()
            except UnicodeError:
                continue
            if nome and (nome not in vistas or rssi > vistas[nome][0]):
                vistas[nome] = (rssi, seguranca != 0)
        ordem = sorted(vistas.items(), key=lambda item: -item[1][0])[:maximo]
        return [{"ssid": nome, "rssi": rssi, "protegida": protegida}
                for nome, (rssi, protegida) in ordem]

    async def manter(self):
        """Tarefa que roda para sempre: conecta, vigia e reconecta."""
        falhas = 0
        while True:
            self._pedido.clear()
            ssid, senha, _ = ler_config()
            self.ssid = ssid
            if not ssid:
                self.ip = None
                self._mudar(SEM_CONFIG)
                await self._pedido.wait()  # dorme até o app mandar uma rede
                continue

            await self._conectar(ssid, senha)
            if self.estado == CONECTADO:
                falhas = 0
                await self._vigiar()       # volta quando cair ou houver pedido
            else:
                espera = ESPERAS_NOVA_TENTATIVA_MS[min(falhas, len(ESPERAS_NOVA_TENTATIVA_MS) - 1)]
                falhas += 1
                # Espera antes de tentar de novo, mas acorda na hora se o
                # app mandar uma senha nova.
                try:
                    await asyncio.wait_for_ms(self._pedido.wait(), espera)
                    falhas = 0  # rede nova: recomeça a contagem
                except asyncio.TimeoutError:
                    pass

    # -----------------------------------------------------------------------
    # Partes internas
    # -----------------------------------------------------------------------
    def _mudar(self, estado):
        if estado == self.estado:
            return
        self.estado = estado
        if estado == CONECTADO:
            print("Wi-Fi: conectado em '%s', IP %s" % (self.ssid, self.ip))
        else:
            print("Wi-Fi:", NOMES_ESTADO[estado])
        if self.ao_mudar:
            self.ao_mudar()

    def _ler_rssi(self):
        try:
            return self.wlan.status("rssi")
        except (OSError, ValueError):
            return None

    async def _conectar(self, ssid, senha):
        self.ip = None
        self.rssi = None
        self._mudar(CONECTANDO)
        self.wlan.active(True)
        self.wlan.disconnect()
        self.wlan.connect(ssid, senha)  # não trava: a conexão segue em segundo plano

        # Status de erro do chip de Wi-Fi -> nosso estado
        falhas = {network.STAT_WRONG_PASSWORD: SENHA_ERRADA,
                  network.STAT_NO_AP_FOUND: REDE_AUSENTE,
                  network.STAT_CONNECT_FAIL: FALHOU}
        falha_atual = None
        falha_desde = 0

        inicio = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), inicio) < TEMPO_LIMITE_CONEXAO_MS:
            status = self.wlan.status()
            agora = time.ticks_ms()
            if status == network.STAT_GOT_IP:
                self.ip = self.wlan.ifconfig()[0]
                self.rssi = self._ler_rssi()
                self._mudar(CONECTADO)
                return
            if status in falhas:
                # O chip às vezes mostra um erro passageiro no meio da
                # conexão; só desistimos se o erro se repetir por 2 s.
                if status != falha_atual:
                    falha_atual, falha_desde = status, agora
                elif time.ticks_diff(agora, falha_desde) >= CONFIRMA_FALHA_MS:
                    self.wlan.disconnect()
                    self._mudar(falhas[status])
                    return
            else:
                falha_atual = None
            if self._pedido.is_set():
                return  # chegou outra rede no meio da tentativa: recomeça
            await asyncio.sleep_ms(INTERVALO_STATUS_MS)

        self.wlan.disconnect()
        self._mudar(FALHOU)

    async def _vigiar(self):
        while True:
            try:
                await asyncio.wait_for_ms(self._pedido.wait(), INTERVALO_VIGIA_MS)
                return  # o app mandou outra rede
            except asyncio.TimeoutError:
                pass
            if not self.wlan.isconnected():
                print("Wi-Fi: conexão perdida, reconectando...")
                return
            self.rssi = self._ler_rssi()

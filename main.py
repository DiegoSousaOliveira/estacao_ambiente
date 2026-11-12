"""
main.py - Estação Ambiente (BitDogLab + AHT10)

Sensor + OLED + matriz de LEDs, LED RGB, buzzer, Wi-Fi e app do computador.

Organização: várias tarefas do asyncio rodando "ao mesmo tempo".
  - tarefa_leitura  -> lê o AHT10 a cada 2 s, atualiza o Estado e pisca o LED
  - tarefa_display  -> redesenha o OLED quando há novidade (ou a cada 1 s)
  - matriz.animar   -> desenha o termômetro de cores (25 quadros/s)
  - tarefa_alarme   -> bipa enquanto a temperatura passar de 32 °C
  - tarefa_botoes   -> botão A troca a tela; botão B silencia o alarme
  - rede.manter     -> conecta no Wi-Fi e reconecta se cair
  - ponte.escutar   -> recebe comandos do app do computador pelo USB
  - tarefa_servidor -> quando o Wi-Fi conecta, abre o painel web na porta 80
Cada tarefa cede a vez com "await" enquanto espera, em vez de travar
tudo com time.sleep(). As tarefas conversam pelo objeto Estado e pelo
evento "redesenhar".
"""

import gc
import sys
import time

from machine import I2C, Pin, SoftI2C

try:
    import asyncio
except ImportError:  # firmwares antigos só têm o nome uasyncio
    import uasyncio as asyncio

import medidas
from aht10 import AHT10, ENDERECO_PADRAO as ENDERECO_AHT10
from display import Display, ENDERECO_OLED, TELA_WIFI
from matriz import Matriz
from perifericos import Botao, Buzzer, LedRGB
from ponte_usb import PonteUSB
from rede import Rede
from servidor import Servidor

# ---------------------------------------------------------------------------
# Pinos da BitDogLab (confira na documentação da sua versão da placa)
# ---------------------------------------------------------------------------
# A BitDogLab tem dois conectores I2C externos. O programa procura o AHT10
# nos dois, então tanto faz em qual ele foi ligado.
#
# O RP2040 só tem dois controladores I2C de hardware (I2C0 e I2C1), e o
# I2C1 já está ocupado pelo OLED nos pinos GP14/GP15. Por isso o conector
# "I2C1" (GP2/GP3) usa SoftI2C: o protocolo é feito por software. É mais
# lento, mas o AHT10 troca só ~10 bytes a cada 2 s, então não faz diferença.
#   (nome curto, descrição, id do I2C de hardware ou None = SoftI2C, SDA, SCL)
CONECTORES_SENSOR = (
    ("I2C0", "externo (GP0/GP1)", 0, 0, 1),
    ("I2C1", "externo (GP2/GP3)", None, 2, 3),
)

I2C_OLED_ID = 1        # OLED interno da placa
PINO_OLED_SDA = 14
PINO_OLED_SCL = 15

PINO_BOTAO_A = 5       # troca de tela (pull-up: solto = 1, apertado = 0)
PINO_BOTAO_B = 6       # silencia o alarme

PINO_MATRIZ = 7        # dados da matriz 5x5 WS2812
PINO_LED_R = 13        # LED RGB
PINO_LED_G = 11
PINO_LED_B = 12
PINO_BUZZER = 21

# 100 kHz no sensor: mais tolerante a cabos longos e placas adaptadoras.
# 400 kHz no OLED: as trilhas são curtas e cada tela tem 1024 bytes; a
# 100 kHz o envio levaria ~95 ms, a 400 kHz leva ~25 ms.
FREQ_I2C_SENSOR = 100000
FREQ_I2C_OLED = 400000
TIMEOUT_SOFTI2C_US = 1000         # espera máxima por "clock stretching" no SoftI2C

# ---------------------------------------------------------------------------
# Comportamento
# ---------------------------------------------------------------------------
INTERVALO_LEITURA_MS = 2000
TAMANHO_MEDIA = 5                 # média móvel das últimas 5 leituras
TAMANHO_HISTORICO = 60            # pontos do gráfico
FALHAS_PARA_ALERTA = 3            # leituras seguidas com erro até a tela avisar
INTERVALO_NOVO_SCAN_MS = 3000     # se o sensor não aparecer, tenta de novo
TEMPO_TELA_INICIAL_MS = 2000      # quanto tempo o resultado do scan fica na tela
INTERVALO_TELA_MS = 1000          # redesenho mínimo (relógio da tela de informações)
INTERVALO_BOTOES_MS = 20          # frequência de leitura dos botões
INTERVALO_BIPES_MS = 10000        # com alarme ativo, um bipe curto a cada 10 s
INTERVALO_ALARME_MS = 200         # de quanto em quanto tempo o alarme é conferido
INTERVALO_ESPERA_WIFI_MS = 1000   # o painel web espera o Wi-Fi conectar

COR_LEITURA_OK = (0, 255, 0)      # LED RGB: verde = leitura boa
COR_LEITURA_ERRO = (255, 0, 0)    # vermelho = leitura falhou

NOMES_CONHECIDOS = {
    ENDERECO_AHT10: "AHT10",
    ENDERECO_OLED: "OLED SSD1306",
    # Sensores que costumam estar no mesmo kit: ajudam a perceber quando
    # o sensor errado foi ligado no conector.
    0x29: "VL53L0X (distância) - não é o AHT10",
}


# ---------------------------------------------------------------------------
# Estado compartilhado entre as tarefas
# ---------------------------------------------------------------------------
class Estado:
    """Tudo o que as telas, o app do PC (e, depois, a web) precisam saber."""

    def __init__(self, rede):
        self.temperatura = None        # °C, já suavizada pela média móvel
        self.umidade = None            # %, já suavizada
        self.sensacao = None           # índice de calor, °C
        self.orvalho = None            # ponto de orvalho, °C
        self.faixa_temp = None
        self.faixa_umid = None
        self.sensor_ok = False
        self.falhas_seguidas = 0
        self.historico = medidas.Historico(TAMANHO_HISTORICO)        # temperatura
        self.historico_umid = medidas.Historico(TAMANHO_HISTORICO)   # umidade (painel web)
        self.temp_minmax = medidas.MinMax()
        self.umid_minmax = medidas.MinMax()
        self.alarme = medidas.AlarmeCalor()
        self.rede = rede
        self._dia = time.localtime()[2]
        # Tempo ligado: somamos os intervalos em vez de usar ticks_ms()
        # direto, porque o contador de ticks "dá a volta" a cada ~12 dias.
        self._ms_ligado = 0
        self._ultimo_tick = time.ticks_ms()

    def nova_leitura(self, temperatura, umidade):
        self.temperatura = temperatura
        self.umidade = umidade
        self.sensacao = medidas.sensacao_termica(temperatura, umidade)
        self.orvalho = medidas.ponto_orvalho(temperatura, umidade)
        self.faixa_temp = medidas.faixa_temperatura(temperatura)
        self.faixa_umid = medidas.faixa_umidade(umidade)
        self.historico.adicionar(temperatura)
        self.historico_umid.adicionar(umidade)
        self.alarme.atualizar(temperatura)

        # Mínimas e máximas "do dia": zeram quando a data muda.
        dia = time.localtime()[2]
        if dia != self._dia:
            self._dia = dia
            self.temp_minmax.zerar()
            self.umid_minmax.zerar()
        self.temp_minmax.adicionar(temperatura)
        self.umid_minmax.adicionar(umidade)

        self.sensor_ok = True
        self.falhas_seguidas = 0

    def falha_leitura(self):
        self.falhas_seguidas += 1
        # Uma falha isolada não apaga a tela: só depois de algumas seguidas.
        if self.falhas_seguidas >= FALHAS_PARA_ALERTA:
            self.sensor_ok = False

    @property
    def ip(self):
        return self.rede.ip

    def segundos_ligado(self):
        agora = time.ticks_ms()
        self._ms_ligado += time.ticks_diff(agora, self._ultimo_tick)
        self._ultimo_tick = agora
        return self._ms_ligado // 1000


# ---------------------------------------------------------------------------
# Diagnóstico de inicialização
# ---------------------------------------------------------------------------
def descrever_placa():
    """Mostra qual placa e firmware estão rodando.

    Pico W e Pico 2 W usam firmwares DIFERENTES (RP2040 x RP2350).
    O texto de sys.implementation._machine diz qual foi gravado.
    """
    maquina = getattr(sys.implementation, "_machine", "desconhecida")
    print("=" * 44)
    print(" Estação Ambiente")
    print("=" * 44)
    print("Placa/firmware:", maquina)
    print("MicroPython:   ", sys.version)
    if " W" not in maquina:
        # O firmware do Pico "comum" roda num Pico W, mas sem o módulo de Wi-Fi.
        print("[AVISO] Firmware sem Wi-Fi! Grave o .uf2 do Pico W / Pico 2 W.")


def escanear(nome, i2c):
    """Faz i2c.scan() e imprime os endereços com o nome do dispositivo."""
    enderecos = i2c.scan()
    print("\nScan %s:" % nome)
    if not enderecos:
        print("  nenhum dispositivo encontrado")
    for endereco in enderecos:
        print("  0x%02X  %s" % (endereco, NOMES_CONHECIDOS.get(endereco, "?")))
    return enderecos


def criar_barramento(id_i2c, sda, scl):
    if id_i2c is None:
        # Com o conector vazio, as linhas ficam "flutuando" e o SoftI2C acha
        # que algum dispositivo está segurando o clock: espera 50 ms em cada
        # um dos 112 endereços, travando a placa por ~6 s. O pull-up interno
        # mantém as linhas em 1 e o timeout curto limita a espera.
        Pin(sda, Pin.IN, Pin.PULL_UP)
        Pin(scl, Pin.IN, Pin.PULL_UP)
        return SoftI2C(sda=Pin(sda), scl=Pin(scl), freq=FREQ_I2C_SENSOR,
                       timeout=TIMEOUT_SOFTI2C_US)
    return I2C(id_i2c, sda=Pin(sda), scl=Pin(scl), freq=FREQ_I2C_SENSOR)


def procurar_sensor():
    """Varre os conectores externos.

    Devolve (resultados, achado):
      resultados -> lista de (nome_curto, enderecos), para mostrar no OLED
      achado     -> (nome_curto, i2c) do conector com o AHT10, ou None
    """
    resultados = []
    achado = None
    for nome, descricao, id_i2c, sda, scl in CONECTORES_SENSOR:
        i2c = criar_barramento(id_i2c, sda, scl)
        enderecos = escanear("%s %s" % (nome, descricao), i2c)
        resultados.append((nome, enderecos))
        if achado is None and ENDERECO_AHT10 in enderecos:
            achado = (nome, i2c)
    return resultados, achado


def explicar_sensor_ausente():
    print("\n[ERRO] AHT10 (0x38) não encontrado em nenhum conector externo.")
    print("  - Confira VCC (3V3), GND, SDA e SCL no conector.")
    print("  - SDA e SCL invertidos é o erro mais comum.")
    print("  Tentando de novo em %d s..." % (INTERVALO_NOVO_SCAN_MS // 1000))


# ---------------------------------------------------------------------------
# Conversa com o app do computador
# ---------------------------------------------------------------------------
def _arredondar(valor):
    return None if valor is None else round(valor, 1)


def dados_para_pc(estado, display):
    tm, um = estado.temp_minmax, estado.umid_minmax
    if not estado.alarme.ativo:
        alarme = "desligado"
    else:
        alarme = "silenciado" if estado.alarme.silenciado else "tocando"
    return {
        "ok": estado.sensor_ok,
        "t": _arredondar(estado.temperatura),
        "u": _arredondar(estado.umidade),
        "st": _arredondar(estado.sensacao),
        "po": _arredondar(estado.orvalho),
        "ft": None if estado.faixa_temp is None else medidas.NOMES_FAIXA_TEMP[estado.faixa_temp],
        "fu": None if estado.faixa_umid is None else medidas.NOMES_FAIXA_UMID[estado.faixa_umid],
        "tmin": _arredondar(tm.minimo), "tmax": _arredondar(tm.maximo),
        "umin": _arredondar(um.minimo), "umax": _arredondar(um.maximo),
        "alarme": alarme,
        "ligado": estado.segundos_ligado(),
        "tela": display.tela,
    }


def wifi_para_pc(rede):
    return {"estado": rede.nome_estado, "ssid": rede.ssid,
            "ip": rede.ip, "rssi": rede.rssi}


def dados_para_web(estado, display):
    """O mesmo resumo do app do PC, mais o histórico para os gráficos."""
    dados = dados_para_pc(estado, display)
    dados["hist_t"] = [round(v, 2) for v in estado.historico.valores()]
    dados["hist_u"] = [round(v, 1) for v in estado.historico_umid.valores()]
    dados["intervalo"] = INTERVALO_LEITURA_MS // 1000
    dados["ip"] = estado.rede.ip
    dados["rede"] = estado.rede.ssid
    return dados


def criar_tratador(estado, display, rede, ponte, redesenhar):
    """Monta a função que responde aos comandos vindos do app do PC."""

    def responder(ok, mensagem):
        ponte.enviar("RESP", {"ok": ok, "msg": mensagem})

    def tratar(comando, primeiro_contato):
        tipo = comando.get("cmd")
        if primeiro_contato:
            print("App do computador conectado.")
            ponte.enviar("WIFI", wifi_para_pc(rede))
            ponte.enviar("DADOS", dados_para_pc(estado, display))
            redesenhar.set()  # manda a imagem do OLED na hora

        if tipo == "ola":
            return  # só um "ainda estou aqui"

        if tipo == "botao":
            if comando.get("qual") == "A":
                display.proxima_tela()
                redesenhar.set()
            elif comando.get("qual") == "B":
                resultado = estado.alarme.alternar_silencio()
                if resultado:
                    print("Alarme %s pelo app." % resultado)
                    redesenhar.set()
                    # devolve o estado na hora, para o botão do app trocar o texto
                    ponte.enviar("DADOS", dados_para_pc(estado, display))
                else:
                    responder(True, "Nenhum alarme ativo agora (ele liga acima de 32 °C).")

        elif tipo == "wifi":
            ssid = (comando.get("ssid") or "").strip()
            senha = comando.get("senha") or ""
            # Limites do padrão Wi-Fi (WPA2): nome até 32, senha de 8 a 63
            if not ssid or len(ssid) > 32:
                responder(False, "O nome da rede deve ter de 1 a 32 caracteres.")
                return
            if senha and not 8 <= len(senha) <= 63:
                responder(False, "A senha do Wi-Fi deve ter de 8 a 63 caracteres.")
                return
            rede.configurar(ssid, senha)  # a senha nunca é impressa
            print("Nova rede Wi-Fi recebida do app:", ssid)
            responder(True, "Salvo na placa. Conectando em \"%s\"..." % ssid)
            display.tela = TELA_WIFI  # mostra o progresso no OLED também
            redesenhar.set()

        elif tipo == "redes":
            # O scan trava a placa por ~3 s; a resposta chega no fim.
            ponte.enviar("REDES", rede.procurar_redes())

        else:
            responder(False, "Comando desconhecido: %s" % tipo)

    return tratar


# ---------------------------------------------------------------------------
# Tarefas
# ---------------------------------------------------------------------------
async def tarefa_leitura(sensor, estado, redesenhar, matriz, led, ponte, display):
    media_temp = medidas.MediaMovel(TAMANHO_MEDIA)
    media_umid = medidas.MediaMovel(TAMANHO_MEDIA)

    print("\n temp  média |  umid  média | sensação orvalho | faixas")
    while True:
        inicio = time.ticks_ms()

        leitura = await sensor.ler_async()
        if leitura is None:
            estado.falha_leitura()
            print("[ERRO] leitura falhou: %s (falhas: %d)"
                  % (sensor.ultimo_erro, sensor.erros))
            if not estado.sensor_ok:
                matriz.mostrar_sem_sensor()
            cor_pisca = COR_LEITURA_ERRO
        else:
            temperatura, umidade = leitura
            estado.nova_leitura(media_temp.adicionar(temperatura),
                                media_umid.adicionar(umidade))
            matriz.mostrar_temperatura(estado.temperatura)
            print("%5.2f  %5.2f | %5.1f  %5.1f |   %5.1f   %5.1f  | %s / UR %s"
                  % (temperatura, estado.temperatura, umidade, estado.umidade,
                     estado.sensacao, estado.orvalho,
                     medidas.NOMES_FAIXA_TEMP[estado.faixa_temp],
                     medidas.NOMES_FAIXA_UMID[estado.faixa_umid]))
            cor_pisca = COR_LEITURA_OK
        redesenhar.set()
        ponte.enviar("DADOS", dados_para_pc(estado, display))
        ponte.enviar("WIFI", wifi_para_pc(estado.rede))  # atualiza a força do sinal
        await led.piscar(*cor_pisca)  # "batimento": mostra que a placa está viva

        # Desconta o tempo gasto na medição e no pisca para manter o ritmo de 2 s.
        # ticks_diff lida com o "estouro" do contador de milissegundos.
        decorrido = time.ticks_diff(time.ticks_ms(), inicio)
        await asyncio.sleep_ms(max(0, INTERVALO_LEITURA_MS - decorrido))


async def tarefa_display(display, estado, redesenhar):
    while True:
        # Espera alguém pedir redesenho, mas no máximo 1 s: assim o
        # "tempo ligado" da tela de informações continua andando.
        try:
            await asyncio.wait_for_ms(redesenhar.wait(), INTERVALO_TELA_MS)
        except asyncio.TimeoutError:
            pass
        redesenhar.clear()
        display.desenhar(estado)


async def tarefa_alarme(estado, buzzer):
    ultimo_bipe = None
    while True:
        # Sem leitura confiável, não faz sentido alarmar com um valor velho.
        if estado.alarme.tocando and estado.sensor_ok:
            agora = time.ticks_ms()
            if ultimo_bipe is None or time.ticks_diff(agora, ultimo_bipe) >= INTERVALO_BIPES_MS:
                ultimo_bipe = agora
                await buzzer.bipe()
        else:
            ultimo_bipe = None  # quando o alarme voltar, bipa na mesma hora
        await asyncio.sleep_ms(INTERVALO_ALARME_MS)


async def tarefa_botoes(display, estado, redesenhar):
    botao_a = Botao(PINO_BOTAO_A)
    botao_b = Botao(PINO_BOTAO_B)
    while True:
        if botao_a.apertou():
            display.proxima_tela()
            redesenhar.set()  # troca a tela na hora, sem esperar a leitura
        if botao_b.apertou():
            # 1º toque silencia, 2º toque faz voltar a tocar. Ao reativar, a
            # tarefa_alarme bipa na hora, o que serve de confirmação.
            resultado = estado.alarme.alternar_silencio()
            if resultado:
                print("Alarme %s pelo botão B." % resultado)
                redesenhar.set()  # troca o ícone do alto-falante na hora
        await asyncio.sleep_ms(INTERVALO_BOTOES_MS)


async def tarefa_servidor(rede, servidor):
    # O servidor escuta em 0.0.0.0 (todos os endereços), então basta abrir
    # uma vez: se o Wi-Fi cair e voltar, ele continua valendo.
    while not rede.conectado:
        await asyncio.sleep_ms(INTERVALO_ESPERA_WIFI_MS)
    try:
        await servidor.iniciar()
        print("Abra no navegador: http://%s/" % rede.ip)
    except OSError as erro:
        print("[ERRO] não deu para abrir o painel web:", erro)


async def autoteste(led, buzzer, matriz):
    """Mostra na partida que cada saída funciona: vale como teste de hardware."""
    for cor in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
        await led.piscar(*cor, duracao_ms=150)
    await buzzer.bipe(duracao_ms=60)
    await matriz.varredura()


def desligar_saidas():
    """Apaga LEDs e buzzer. Se o programa parar no meio de um bipe,
    o PWM continuaria tocando sozinho."""
    for pino in (PINO_BUZZER, PINO_LED_R, PINO_LED_G, PINO_LED_B):
        Pin(pino, Pin.OUT, value=0)
    Matriz(PINO_MATRIZ).apagar()


async def principal():
    descrever_placa()

    led = LedRGB(PINO_LED_R, PINO_LED_G, PINO_LED_B)
    buzzer = Buzzer(PINO_BUZZER)
    matriz = Matriz(PINO_MATRIZ)

    i2c_oled = I2C(I2C_OLED_ID, sda=Pin(PINO_OLED_SDA),
                   scl=Pin(PINO_OLED_SCL), freq=FREQ_I2C_OLED)
    if ENDERECO_OLED not in escanear("OLED interno (GP14/GP15)", i2c_oled):
        print("[AVISO] OLED não respondeu em 0x3C; seguindo só com o terminal.")
    display = Display(i2c_oled)

    # Wi-Fi e app do PC começam antes do sensor: mesmo sem AHT10 dá para
    # configurar a rede e ver a tela de erro espelhada no computador.
    redesenhar = asyncio.Event()
    ponte = PonteUSB()

    def ao_mudar_wifi():
        ponte.enviar("WIFI", wifi_para_pc(rede))
        redesenhar.set()

    rede = Rede(ao_mudar=ao_mudar_wifi)
    estado = Estado(rede)
    display.espelho = ponte.enviar_tela
    ponte.tratar_comando = criar_tratador(estado, display, rede, ponte, redesenhar)
    asyncio.create_task(ponte.escutar())
    asyncio.create_task(rede.manter())
    servidor = Servidor(lambda: dados_para_web(estado, display))
    asyncio.create_task(tarefa_servidor(rede, servidor))

    await autoteste(led, buzzer, matriz)

    # Em vez de parar o programa, espera o sensor ser ligado.
    # Assim dá para conectar o cabo com a placa já rodando.
    while True:
        resultados, achado = procurar_sensor()
        display.tela_scan(resultados, achado[0] if achado else None)
        if achado:
            break
        explicar_sensor_ausente()
        await asyncio.sleep_ms(INTERVALO_NOVO_SCAN_MS)

    nome_conector, i2c_sensor = achado
    print("\nAHT10 encontrado no", nome_conector)
    sensor = AHT10(i2c_sensor)
    print("AHT10 calibrado:", "sim" if sensor.calibrado() else "NÃO (verifique)")

    # Recolhe o lixo da inicialização antes de entrar no laço infinito.
    gc.collect()
    print("Memória livre: %d bytes" % gc.mem_free())

    await asyncio.sleep_ms(TEMPO_TELA_INICIAL_MS)  # deixa o scan visível
    asyncio.create_task(tarefa_display(display, estado, redesenhar))
    asyncio.create_task(tarefa_botoes(display, estado, redesenhar))
    asyncio.create_task(tarefa_alarme(estado, buzzer))
    asyncio.create_task(matriz.animar())
    await tarefa_leitura(sensor, estado, redesenhar, matriz, led, ponte, display)


try:
    asyncio.run(principal())
except KeyboardInterrupt:
    print("\nParado pelo usuário (Ctrl+C).")
finally:
    desligar_saidas()
    # Limpa o estado do asyncio; sem isso, rodar de novo pelo REPL
    # pode reaproveitar tarefas antigas.
    asyncio.new_event_loop()

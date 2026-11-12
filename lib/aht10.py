"""
lib/aht10.py - Driver do sensor AHT10 (temperatura e umidade relativa).

Escrito do zero, sem bibliotecas externas, para que dê para estudar
exatamente o que trafega no barramento I2C. Tudo segue o datasheet do
fabricante (Aosong):

  1. Inicialização: envia 0xE1 0x08 0x00 e espera 20 ms.
     (o 0x08 liga a calibração de fábrica; sem ela as leituras saem erradas)
  2. Medição: envia 0xAC 0x33 0x00, espera ~80 ms e lê 6 bytes:
       byte 0      -> status (bit 7 = ocupado, bit 3 = calibrado)
       bytes 1..3  -> 20 bits de umidade (a metade alta do byte 3)
       bytes 3..5  -> 20 bits de temperatura (a metade baixa do byte 3)
  3. Conversão (2^20 = 1 048 576 é a quantidade de valores em 20 bits):
       UR (%)  = bruto_umid / 2^20 * 100
       T  (°C) = bruto_temp / 2^20 * 200 - 50

Uso rápido no REPL:
    >>> from machine import I2C, Pin
    >>> from aht10 import AHT10
    >>> s = AHT10(I2C(0, sda=Pin(0), scl=Pin(1)))
    >>> s.ler()
    (24.81, 61.3)
"""

import time

# ---------------------------------------------------------------------------
# Constantes do sensor (datasheet AHT10)
# ---------------------------------------------------------------------------
ENDERECO_PADRAO = 0x38

CMD_INICIALIZAR = b"\xE1\x08\x00"  # inicializa e liga a calibração
CMD_MEDIR = b"\xAC\x33\x00"        # dispara uma medição
CMD_RESET = b"\xBA"                # reset por software

TEMPO_INICIALIZACAO_MS = 20
TEMPO_RESET_MS = 20
TEMPO_MEDICAO_MS = 80              # o datasheet pede "mais de 75 ms"
TEMPO_ESPERA_OCUPADO_MS = 10       # espera extra se o sensor ainda estiver ocupado
MAX_TENTATIVAS = 5                 # 80 ms + 5 x 10 ms: depois disso é defeito

BIT_OCUPADO = 0x80                 # bit 7 do byte de status
BIT_CALIBRADO = 0x08               # bit 3 do byte de status

ESCALA = 1 << 20                   # 2^20 = 1 048 576
MAX_20_BITS = ESCALA - 1           # 0xFFFFF


class AHT10:
    """Sensor AHT10 ligado num barramento I2C já criado.

    ler()        -> versão bloqueante (~80 ms), boa para testar no REPL.
    ler_async()  -> mesma coisa, mas libera o processador durante a espera;
                    é a que o main.py usa junto com o asyncio.

    As duas devolvem (temperatura_C, umidade_pct) ou None se algo deu errado.
    Nesse caso o motivo fica em .ultimo_erro. Nenhuma das duas levanta
    exceção: um fio solto não pode travar a estação inteira.
    """

    def __init__(self, i2c, endereco=ENDERECO_PADRAO):
        self.i2c = i2c
        self.endereco = endereco
        self.ultimo_erro = None
        self.erros = 0  # contador de falhas, útil para diagnóstico
        # Buffer reaproveitado em todas as leituras: evita criar um objeto
        # novo a cada 2 s, o que fragmentaria a pouca RAM do Pico.
        self._buf = bytearray(6)
        self._precisa_iniciar = True
        self.iniciar()

    # -----------------------------------------------------------------------
    # Inicialização
    # -----------------------------------------------------------------------
    def iniciar(self):
        """Envia o comando de inicialização. Devolve True se deu certo.

        Usa sleep bloqueante de 20 ms, mas isso só acontece na partida
        ou depois de uma falha, então não atrapalha o laço principal.
        """
        try:
            self.i2c.writeto(self.endereco, CMD_INICIALIZAR)
            time.sleep_ms(TEMPO_INICIALIZACAO_MS)
            if not (self._ler_status() & BIT_CALIBRADO):
                # Alguns módulos só aceitam a calibração depois de um reset.
                # Se mesmo assim o bit não subir, seguimos em frente: há
                # clones que nunca ligam esse bit e ainda medem certo.
                self.i2c.writeto(self.endereco, CMD_RESET)
                time.sleep_ms(TEMPO_RESET_MS)
                self.i2c.writeto(self.endereco, CMD_INICIALIZAR)
                time.sleep_ms(TEMPO_INICIALIZACAO_MS)
            self._precisa_iniciar = False
            self.ultimo_erro = None
            return True
        except OSError as erro:
            self._falha_i2c(erro)
            return False

    def calibrado(self):
        """True se o sensor diz estar calibrado (bit 3 do status)."""
        try:
            return bool(self._ler_status() & BIT_CALIBRADO)
        except OSError:
            return False

    # -----------------------------------------------------------------------
    # Leitura
    # -----------------------------------------------------------------------
    def ler(self):
        """Mede e devolve (temperatura, umidade). Bloqueia por ~80 ms."""
        if not self._pronto_para_medir():
            return None
        try:
            self.i2c.writeto(self.endereco, CMD_MEDIR)
            time.sleep_ms(TEMPO_MEDICAO_MS)
            for _ in range(MAX_TENTATIVAS):
                if self._medicao_pronta():
                    return self._converter()
                time.sleep_ms(TEMPO_ESPERA_OCUPADO_MS)
            return self._falha("sensor continuou ocupado")
        except OSError as erro:
            return self._falha_i2c(erro)

    async def ler_async(self):
        """Igual a ler(), mas cede o processador durante as esperas.

        Enquanto o sensor mede (80 ms), o asyncio pode atualizar o display,
        a matriz de LEDs ou responder ao navegador.
        """
        # Import aqui dentro: quem só usa ler() no REPL não paga a RAM do asyncio.
        try:
            import asyncio
        except ImportError:
            import uasyncio as asyncio

        if not self._pronto_para_medir():
            return None
        try:
            self.i2c.writeto(self.endereco, CMD_MEDIR)
            await asyncio.sleep_ms(TEMPO_MEDICAO_MS)
            for _ in range(MAX_TENTATIVAS):
                if self._medicao_pronta():
                    return self._converter()
                await asyncio.sleep_ms(TEMPO_ESPERA_OCUPADO_MS)
            return self._falha("sensor continuou ocupado")
        except OSError as erro:
            return self._falha_i2c(erro)

    # -----------------------------------------------------------------------
    # Partes internas
    # -----------------------------------------------------------------------
    def _pronto_para_medir(self):
        # Se a última comunicação falhou (ex.: cabo foi desconectado e
        # reconectado), o sensor pode ter reiniciado e precisa do 0xE1 de novo.
        if self._precisa_iniciar:
            return self.iniciar()
        return True

    def _ler_status(self):
        return self.i2c.readfrom(self.endereco, 1)[0]

    def _medicao_pronta(self):
        """Lê os 6 bytes no buffer e diz se o bit 'ocupado' já baixou."""
        self.i2c.readfrom_into(self.endereco, self._buf)
        return not (self._buf[0] & BIT_OCUPADO)

    def _converter(self):
        b = self._buf
        # Os 20 bits de umidade começam no byte 1 e terminam na metade
        # alta do byte 3; por isso os deslocamentos 12, 4 e o >> 4.
        bruto_umid = (b[1] << 12) | (b[2] << 4) | (b[3] >> 4)
        # A temperatura começa na metade baixa do byte 3 (& 0x0F).
        bruto_temp = ((b[3] & 0x0F) << 16) | (b[4] << 8) | b[5]

        # Tudo zero ou tudo 1 indica barramento com problema, não um valor real.
        if bruto_umid in (0, MAX_20_BITS) or bruto_temp in (0, MAX_20_BITS):
            return self._falha("dados inválidos do sensor", reiniciar=True)

        umidade = bruto_umid * 100 / ESCALA
        temperatura = bruto_temp * 200 / ESCALA - 50
        self.ultimo_erro = None
        return temperatura, umidade

    def _falha(self, motivo, reiniciar=False):
        self.ultimo_erro = motivo
        self.erros += 1
        if reiniciar:
            self._precisa_iniciar = True
        return None

    def _falha_i2c(self, erro):
        # errno 5 (EIO) = ninguém respondeu no endereço; 110 = tempo esgotado.
        codigo = erro.args[0] if erro.args else "?"
        if codigo == 5:
            motivo = "sensor não respondeu (EIO) - confira os fios"
        elif codigo == 110:
            motivo = "tempo esgotado no I2C (ETIMEDOUT)"
        else:
            motivo = "erro de I2C: %s" % codigo
        return self._falha(motivo, reiniciar=True)

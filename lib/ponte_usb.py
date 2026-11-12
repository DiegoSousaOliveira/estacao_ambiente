"""
lib/ponte_usb.py - Conversa com o app do computador pelo cabo USB.

O cabo USB do Pico é uma porta serial: tudo o que o programa imprime com
print() chega no computador, e o que o computador escreve chega em
sys.stdin. O app do PC usa esse mesmo canal, com um protocolo simples de
linhas de texto que começam com "@":

  placa -> PC   @TELA <imagem do OLED em base64>
                @DADOS {...}   @WIFI {...}   @REDES [...]   @RESP {...}
  PC -> placa   @{"cmd": "ola"}                 (a cada 2 s: "ainda estou aqui")
                @{"cmd": "botao", "qual": "A"}
                @{"cmd": "wifi", "ssid": "...", "senha": "..."}
                @{"cmd": "redes"}

Linhas sem "@" continuam sendo o terminal normal (as leituras impressas),
e o app as mostra numa caixa de log. A placa só envia a imagem do OLED
enquanto o app estiver dando sinal de vida; assim o terminal do MicroPico
não fica cheio de texto codificado.
"""

import json
import select
import sys
import time

from binascii import b2a_base64

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

TEMPO_SEM_CONTATO_MS = 6000    # sem "ola" por 6 s = app fechado
TAMANHO_MAXIMO_LINHA = 512     # protege a RAM contra lixo sem quebra de linha
INTERVALO_ESCUTA_MS = 20


class PonteUSB:
    def __init__(self):
        # tratar_comando(cmd, primeiro_contato) é definido pelo main.py
        self.tratar_comando = None
        self._poll = select.poll()
        self._poll.register(sys.stdin, select.POLLIN)
        self._linha = ""
        self._ultimo_contato = None

    @property
    def conectado(self):
        return (self._ultimo_contato is not None and
                time.ticks_diff(time.ticks_ms(), self._ultimo_contato)
                < TEMPO_SEM_CONTATO_MS)

    def enviar(self, tipo, dados):
        if self.conectado:
            print("@%s %s" % (tipo, json.dumps(dados)))

    def enviar_tela(self, buffer):
        # base64 transforma os 1024 bytes da tela em 1368 caracteres de texto
        if self.conectado:
            print("@TELA", b2a_base64(buffer, newline=False).decode())

    async def escutar(self):
        """Tarefa que lê o que chega do PC, um caractere por vez, sem travar."""
        while True:
            # poll(0) só pergunta "tem algo para ler?" e volta na hora
            while self._poll.poll(0):
                caractere = sys.stdin.read(1)
                if caractere in "\r\n":
                    if self._linha:
                        self._processar(self._linha)
                        self._linha = ""
                elif len(self._linha) < TAMANHO_MAXIMO_LINHA:
                    self._linha += caractere
            await asyncio.sleep_ms(INTERVALO_ESCUTA_MS)

    def _processar(self, linha):
        if not linha.startswith("@"):
            return  # texto digitado no terminal do MicroPico: ignora
        try:
            comando = json.loads(linha[1:])
        except ValueError:
            return
        primeiro_contato = not self.conectado
        self._ultimo_contato = time.ticks_ms()
        if self.tratar_comando is None:
            return
        try:
            self.tratar_comando(comando, primeiro_contato)
        except Exception as erro:  # um comando ruim não pode derrubar a estação
            self.enviar("RESP", {"ok": False, "msg": "erro na placa: %s" % erro})

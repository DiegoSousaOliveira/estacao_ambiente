"""
lib/servidor.py - Painel web servido pela própria placa.

Um servidor HTTP mínimo, feito com asyncio.start_server: cada navegador
que se conecta vira uma tarefa, então o sensor, o display e a matriz
continuam funcionando enquanto a página é enviada.

Endereços:
  /               -> a página web/index.html (gráficos e cartões)
  /api/leituras   -> JSON com a leitura atual e o histórico; a página
                     busca esse endereço a cada 5 s

Nada é gravado na flash: a página mostra só os dados ao vivo e o histórico
curto que fica na memória.
"""

import gc
import json
import os

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

PORTA = 80
ARQUIVO_PAGINA = "web/index.html"
TAMANHO_PEDACO = 512           # a página sai da flash em pedaços de 512 bytes
TEMPO_LIMITE_MS = 5000         # navegador que não manda o pedido em 5 s é descartado
MAX_CABECALHOS = 40            # proteção contra pedidos gigantes

_TEXTOS_STATUS = {200: "OK", 204: "No Content", 404: "Not Found",
                  405: "Method Not Allowed", 500: "Internal Server Error"}


class Servidor:
    def __init__(self, gerar_dados):
        # gerar_dados() devolve o dicionário que vira o JSON de /api/leituras
        self.gerar_dados = gerar_dados
        self.atendimentos = 0

    async def iniciar(self):
        await asyncio.start_server(self._atender, "0.0.0.0", PORTA)
        print("Painel web no ar (porta %d)." % PORTA)

    async def _atender(self, leitor, escritor):
        try:
            pedido = await asyncio.wait_for_ms(leitor.readline(), TEMPO_LIMITE_MS)
            partes = pedido.decode().split()
            if len(partes) < 2:
                return
            metodo, caminho = partes[0], partes[1].split("?")[0]

            # Lemos e descartamos os cabeçalhos: não precisamos de nenhum deles,
            # mas o navegador só espera a resposta depois de enviá-los.
            for _ in range(MAX_CABECALHOS):
                linha = await asyncio.wait_for_ms(leitor.readline(), TEMPO_LIMITE_MS)
                if not linha or linha == b"\r\n":
                    break

            self.atendimentos += 1
            if metodo != "GET":
                await self._responder(escritor, 405, "text/plain", "use GET")
            elif caminho in ("/", "/index.html"):
                await self._enviar_pagina(escritor)
            elif caminho == "/api/leituras":
                corpo = json.dumps(self.gerar_dados())
                await self._responder(escritor, 200, "application/json", corpo)
            elif caminho == "/favicon.ico":
                await self._responder(escritor, 204, "text/plain", "")
            else:
                await self._responder(escritor, 404, "text/plain", "nada aqui")
        except (OSError, ValueError, asyncio.TimeoutError):
            pass  # navegador fechou ou demorou: só encerramos a conexão
        finally:
            try:
                escritor.close()
                await escritor.wait_closed()
            except OSError:
                pass
            # Cada atendimento cria strings e buffers temporários; recolher
            # aqui evita que a memória fragmente com o painel aberto por horas.
            gc.collect()

    async def _responder(self, escritor, status, tipo, corpo):
        corpo = corpo.encode() if isinstance(corpo, str) else corpo
        escritor.write(_cabecalho(status, tipo, len(corpo)))
        if corpo:
            escritor.write(corpo)
        await escritor.drain()

    async def _enviar_pagina(self, escritor):
        try:
            tamanho = os.stat(ARQUIVO_PAGINA)[6]
        except OSError:
            await self._responder(escritor, 500, "text/plain",
                                  "web/index.html nao foi enviado para a placa")
            return
        escritor.write(_cabecalho(200, "text/html; charset=utf-8", tamanho))
        await escritor.drain()
        # Em pedaços: a página inteira (~12 KB) de uma vez ocuparia RAM à toa.
        with open(ARQUIVO_PAGINA, "rb") as arquivo:
            while True:
                pedaco = arquivo.read(TAMANHO_PEDACO)
                if not pedaco:
                    break
                escritor.write(pedaco)
                await escritor.drain()  # espera o Wi-Fi enviar antes de ler mais


def _cabecalho(status, tipo, tamanho):
    # no-store: o navegador sempre pede dados novos em vez de usar uma cópia velha
    return ("HTTP/1.0 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
            "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
            % (status, _TEXTOS_STATUS.get(status, ""), tipo, tamanho)).encode()

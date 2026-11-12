"""
lib/matriz.py - Matriz 5x5 de LEDs WS2812 como "termômetro de cores".

Funciona como um termômetro de barra: as linhas acendem de baixo para
cima conforme a temperatura sobe, e a cor acompanha a faixa:

    azul -> verde-azulado -> âmbar -> laranja -> vermelho
    (frio)  (confortável)    (quente ...........)  (muito quente)

A cor e a altura da barra não pulam: a cada quadro da animação elas
andam um pedaço do caminho até o valor novo, o que dá a transição suave.

Coordenadas: linha 0 é a de cima e coluna 0 é a da esquerda, como numa
tabela. A função indice() traduz isso para a posição do LED no fio.
"""

import neopixel
from machine import Pin

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

LINHAS = 5
COLUNAS = 5
NUM_LEDS = LINHAS * COLUNAS

# Os WS2812 são muito fortes: 10 % já é bem visível e não ofusca ninguém.
# Também reduz o consumo: 25 LEDs em branco total puxariam ~1,5 A.
BRILHO_MAXIMO = 0.10

INTERVALO_QUADRO_MS = 40     # 25 quadros por segundo
SUAVIZACAO = 0.12            # fração do caminho até o alvo andada por quadro (~1 s)

# Altura da barra: 15 °C ou menos acende só a linha de baixo, 35 °C acende todas.
TEMP_BARRA_MINIMA = 15.0
TEMP_BARRA_MAXIMA = 35.0

# Pontos da escala de cores (°C, (R, G, B) com brilho total).
# Entre dois pontos a cor é misturada proporcionalmente.
PARADAS_COR = (
    (17.0, (0, 70, 255)),    # azul           - frio
    (22.0, (0, 200, 150)),   # verde-azulado  - confortável
    (27.0, (255, 170, 0)),   # âmbar          - começo do quente
    (30.0, (255, 90, 0)),    # laranja
    (33.0, (255, 0, 0)),     # vermelho       - muito quente
)

COR_VARREDURA = (255, 255, 255)
COR_SEM_SENSOR = (255, 255, 255)


def indice(linha, coluna):
    """Converte (linha, coluna) no número do LED dentro da corrente.

    Na BitDogLab os 25 LEDs formam uma única "cobra" em zigue-zague que
    começa no canto de baixo à direita:

        24 23 22 21 20     <- linha 0 (topo)
        15 16 17 18 19
        14 13 12 11 10
         5  6  7  8  9
         4  3  2  1  0     <- linha 4 (base); o LED 0 é o primeiro do fio

    Contando as linhas de baixo para cima, as pares (0, 2, 4) correm da
    direita para a esquerda e as ímpares, da esquerda para a direita.
    """
    linha_de_baixo = LINHAS - 1 - linha
    if linha_de_baixo % 2 == 0:
        return linha_de_baixo * COLUNAS + (COLUNAS - 1 - coluna)
    return linha_de_baixo * COLUNAS + coluna


def cor_da_temperatura(t):
    """Cor (R, G, B) da escala para a temperatura t, com brilho total."""
    if t <= PARADAS_COR[0][0]:
        return PARADAS_COR[0][1]
    for (t1, c1), (t2, c2) in zip(PARADAS_COR, PARADAS_COR[1:]):
        if t <= t2:
            f = (t - t1) / (t2 - t1)  # 0 no ponto t1, 1 no ponto t2
            return (c1[0] + (c2[0] - c1[0]) * f,
                    c1[1] + (c2[1] - c1[1]) * f,
                    c1[2] + (c2[2] - c1[2]) * f)
    return PARADAS_COR[-1][1]


def nivel_da_temperatura(t):
    """Altura da barra em linhas (de 1 a 5, com fração para a linha parcial)."""
    f = (t - TEMP_BARRA_MINIMA) / (TEMP_BARRA_MAXIMA - TEMP_BARRA_MINIMA)
    f = min(max(f, 0.0), 1.0)
    return 1 + f * (LINHAS - 1)


def _escurecer(cor, fator):
    # int() arredonda para baixo: 255 * 10 % = 25,5 vira 25 e nunca passa do limite
    return (int(cor[0] * fator), int(cor[1] * fator), int(cor[2] * fator))


class Matriz:
    def __init__(self, pino):
        self.np = neopixel.NeoPixel(Pin(pino, Pin.OUT), NUM_LEDS)
        self._cor = [0.0, 0.0, 0.0]      # cor mostrada agora
        self._nivel = 0.0                # altura mostrada agora
        self._alvo_cor = (0, 0, 0)       # para onde a animação está indo
        self._alvo_nivel = 0.0
        self.sem_sensor = False
        self._quadro = 0
        self.apagar()  # LEDs podem ter ficado acesos da execução anterior

    # -----------------------------------------------------------------------
    # API usada pelo main.py
    # -----------------------------------------------------------------------
    def mostrar_temperatura(self, t):
        self._alvo_cor = cor_da_temperatura(t)
        self._alvo_nivel = nivel_da_temperatura(t)
        self.sem_sensor = False

    def mostrar_sem_sensor(self):
        self.sem_sensor = True

    def apagar(self):
        self.np.fill((0, 0, 0))
        self.np.write()

    async def varredura(self, intervalo_ms=40):
        """Acende um LED de cada vez, na ordem de leitura (esquerda->direita,
        cima->baixo). Serve de teste: se a luz não andar nessa ordem, o
        mapeamento de indice() não bate com a sua placa."""
        cor = _escurecer(COR_VARREDURA, BRILHO_MAXIMO)
        for linha in range(LINHAS):
            for coluna in range(COLUNAS):
                self.np.fill((0, 0, 0))
                self.np[indice(linha, coluna)] = cor
                self.np.write()
                await asyncio.sleep_ms(intervalo_ms)
        self.apagar()

    async def animar(self):
        """Tarefa que roda para sempre desenhando um quadro a cada 40 ms."""
        while True:
            self._quadro += 1
            if self.sem_sensor:
                self._quadro_sem_sensor()
            else:
                self._quadro_termometro()
            await asyncio.sleep_ms(INTERVALO_QUADRO_MS)

    # -----------------------------------------------------------------------
    # Quadros da animação
    # -----------------------------------------------------------------------
    def _quadro_termometro(self):
        # Anda uma fração do caminho até o alvo (suavização exponencial)
        for i in range(3):
            self._cor[i] += (self._alvo_cor[i] - self._cor[i]) * SUAVIZACAO
        self._nivel += (self._alvo_nivel - self._nivel) * SUAVIZACAO

        for linha in range(LINHAS):
            # Quanto desta linha está "cheia": 0 = apagada, 1 = acesa inteira.
            # A linha do topo da barra acende parcialmente (brilho menor).
            cheio = self._nivel - (LINHAS - 1 - linha)
            cheio = min(max(cheio, 0.0), 1.0)
            cor = _escurecer(self._cor, cheio * BRILHO_MAXIMO)
            for coluna in range(COLUNAS):
                self.np[indice(linha, coluna)] = cor
        self.np.write()

    def _quadro_sem_sensor(self):
        # Só o LED do centro piscando devagar: "estou ligada, mas sem dados".
        self.np.fill((0, 0, 0))
        if (self._quadro // 12) % 2 == 0:  # ~0,5 s aceso, ~0,5 s apagado
            self.np[indice(2, 2)] = _escurecer(COR_SEM_SENSOR, BRILHO_MAXIMO)
        self.np.write()

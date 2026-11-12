"""
lib/perifericos.py - LED RGB, buzzer e botões da BitDogLab.

Os números dos pinos ficam todos no main.py (o "mapa" da placa); aqui só
estão as classes, que recebem os pinos como parâmetro.
"""

import time

from machine import PWM, Pin

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

# ---------------------------------------------------------------------------
# LED RGB
# ---------------------------------------------------------------------------
# PWM liga e desliga o LED milhares de vezes por segundo; a fração do tempo
# ligado (duty) define o brilho que o olho percebe. Atenção: GP12 e GP13
# usam o mesmo "slice" de PWM do RP2040, então precisam da mesma frequência.
FREQ_PWM_LED = 1000
BRILHO_LED = 0.20            # o LED RGB da placa é forte; 20 % basta
DURACAO_PISCA_MS = 80

# ---------------------------------------------------------------------------
# Buzzer (passivo: só toca se receber uma onda quadrada via PWM)
# ---------------------------------------------------------------------------
FREQ_BIPE = 2000             # Hz: perto da frequência em que o buzzer soa mais alto
DURACAO_BIPE_MS = 120
VOLUME = 0.5                 # duty de 50 % = onda quadrada simétrica, o som mais forte

# ---------------------------------------------------------------------------
# Botões
# ---------------------------------------------------------------------------
TEMPO_ANTIRREPIQUE_MS = 250  # ignora o "repique" mecânico logo após um aperto


class LedRGB:
    def __init__(self, pino_r, pino_g, pino_b):
        self._pwms = [PWM(Pin(p), freq=FREQ_PWM_LED) for p in (pino_r, pino_g, pino_b)]
        self.desligar()

    def cor(self, r, g, b):
        """Acende com (r, g, b) de 0 a 255, já limitado por BRILHO_LED."""
        for pwm, valor in zip(self._pwms, (r, g, b)):
            pwm.duty_u16(int(valor * BRILHO_LED * 65535 / 255))

    def desligar(self):
        self.cor(0, 0, 0)

    async def piscar(self, r, g, b, duracao_ms=DURACAO_PISCA_MS):
        self.cor(r, g, b)
        await asyncio.sleep_ms(duracao_ms)
        self.desligar()


class Buzzer:
    def __init__(self, pino):
        self._pwm = PWM(Pin(pino))
        self.desligar()

    def desligar(self):
        self._pwm.duty_u16(0)

    async def bipe(self, freq=FREQ_BIPE, duracao_ms=DURACAO_BIPE_MS):
        self._pwm.freq(freq)
        self._pwm.duty_u16(int(VOLUME * 65535))
        await asyncio.sleep_ms(duracao_ms)  # o bipe não trava as outras tarefas
        self.desligar()


class Botao:
    """Botão com pull-up interno: solto = 1, apertado = 0.

    É lido periodicamente (polling) em vez de por interrupção: mais simples
    de entender e, lendo a cada ~20 ms, já filtra boa parte do repique.
    """

    def __init__(self, pino):
        self._pino = Pin(pino, Pin.IN, Pin.PULL_UP)
        self._anterior = self._pino.value()
        self._ultimo_aperto = time.ticks_ms()

    def apertou(self):
        """True uma única vez a cada aperto (borda de descida 1 -> 0)."""
        atual = self._pino.value()
        apertou = False
        if self._anterior == 1 and atual == 0:
            agora = time.ticks_ms()
            if time.ticks_diff(agora, self._ultimo_aperto) > TEMPO_ANTIRREPIQUE_MS:
                self._ultimo_aperto = agora
                apertou = True
        self._anterior = atual
        return apertou

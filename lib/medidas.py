"""
lib/medidas.py - Contas feitas com as leituras do sensor.

  - média móvel e histórico das últimas leituras
  - mínima e máxima
  - sensação térmica (índice de calor) e ponto de orvalho
  - faixas de conforto de temperatura e umidade

Fica num arquivo separado porque o display, a matriz de LEDs e o painel
web usam as mesmas regras: assim "quente" significa a mesma coisa em todo
lugar, e para mudar um limite basta editar as constantes abaixo.
"""

import math
from array import array

# ---------------------------------------------------------------------------
# Faixas de conforto
# ---------------------------------------------------------------------------
# Temperatura (°C): frio < 20 | confortável 20-27 | quente 27-32 | muito quente > 32
LIMITE_FRIO = 20.0
LIMITE_CONFORTAVEL = 27.0
LIMITE_QUENTE = 32.0

# Umidade relativa (%): baixa < 30 | ideal 40-60 | alta > 70.
# Os intervalos 30-40 e 60-70 não são "ideais" mas também não preocupam:
# chamamos de "moderada".
LIMITE_UMID_BAIXA = 30.0
LIMITE_UMID_IDEAL_MIN = 40.0
LIMITE_UMID_IDEAL_MAX = 60.0
LIMITE_UMID_ALTA = 70.0

# As faixas são guardadas como números (mais barato que texto) e viram
# nome só na hora de mostrar. Os nomes não têm acento porque a fonte
# embutida do OLED só tem os caracteres ASCII.
FRIO, CONFORTAVEL, QUENTE, MUITO_QUENTE = 0, 1, 2, 3
NOMES_FAIXA_TEMP = ("FRIO", "CONFORTAVEL", "QUENTE", "MUITO QUENTE")

UMID_BAIXA, UMID_MODERADA, UMID_IDEAL, UMID_ALTA = 0, 1, 2, 3
NOMES_FAIXA_UMID = ("BAIXA", "MODERADA", "IDEAL", "ALTA")


def faixa_temperatura(t):
    if t < LIMITE_FRIO:
        return FRIO
    if t <= LIMITE_CONFORTAVEL:
        return CONFORTAVEL
    if t <= LIMITE_QUENTE:
        return QUENTE
    return MUITO_QUENTE


def faixa_umidade(u):
    if u < LIMITE_UMID_BAIXA:
        return UMID_BAIXA
    if u > LIMITE_UMID_ALTA:
        return UMID_ALTA
    if LIMITE_UMID_IDEAL_MIN <= u <= LIMITE_UMID_IDEAL_MAX:
        return UMID_IDEAL
    return UMID_MODERADA


# ---------------------------------------------------------------------------
# Sensação térmica e ponto de orvalho
# ---------------------------------------------------------------------------
def sensacao_termica(t, ur):
    """Índice de calor (°C) pela fórmula da NOAA (serviço de meteorologia dos EUA).

    A fórmula original é em °F, por isso a conversão na entrada e na saída.
    Com ar quente e úmido o suor evapora mal, e o corpo sente mais calor
    do que o termômetro mostra. Em temperaturas amenas a fórmula simples
    de Steadman já basta, e o resultado fica perto da temperatura real.
    """
    f = t * 1.8 + 32
    # Primeira estimativa (Steadman): vale para índices abaixo de 80 °F (~27 °C)
    ic = 0.5 * (f + 61.0 + (f - 68.0) * 1.2 + ur * 0.094)

    if (ic + f) / 2 >= 80:
        # Regressão de Rothfusz: mais precisa no calor
        ic = (-42.379 + 2.04901523 * f + 10.14333127 * ur
              - 0.22475541 * f * ur - 0.00683783 * f * f
              - 0.05481717 * ur * ur + 0.00122874 * f * f * ur
              + 0.00085282 * f * ur * ur - 0.00000199 * f * f * ur * ur)
        # Ajustes da NOAA para ar muito seco ou muito úmido
        if ur < 13 and 80 <= f <= 112:
            ic -= ((13 - ur) / 4) * math.sqrt((17 - abs(f - 95)) / 17)
        elif ur > 85 and 80 <= f <= 87:
            ic += ((ur - 85) / 10) * ((87 - f) / 5)

    return (ic - 32) / 1.8


# Constantes de Magnus (Sonntag, 1990), válidas de -45 °C a 60 °C
_MAGNUS_A = 17.62
_MAGNUS_B = 243.12


def ponto_orvalho(t, ur):
    """Temperatura (°C) em que o vapor do ar começa a condensar.

    É a temperatura em que uma lata gelada começa a "suar". Quanto mais
    perto da temperatura do ar, mais úmido e abafado o ambiente.
    """
    ur = max(ur, 0.1)  # log(0) não existe; 0,1 % é seco o bastante
    gama = math.log(ur / 100) + _MAGNUS_A * t / (_MAGNUS_B + t)
    return _MAGNUS_B * gama / (_MAGNUS_A - gama)


# ---------------------------------------------------------------------------
# Estruturas para guardar leituras
# ---------------------------------------------------------------------------
class MediaMovel:
    """Média das últimas N amostras.

    Suaviza pequenas oscilações: um sopro perto do sensor não faz o
    número da tela pular de uma vez.
    """

    def __init__(self, tamanho):
        self.tamanho = tamanho
        self.valores = []

    def adicionar(self, valor):
        self.valores.append(valor)
        if len(self.valores) > self.tamanho:
            self.valores.pop(0)
        return sum(self.valores) / len(self.valores)


class Historico:
    """As últimas N leituras num buffer circular de tamanho fixo.

    array('f') guarda cada número em 4 bytes, sem criar um objeto novo a
    cada leitura. Quando enche, a leitura nova sobrescreve a mais antiga.
    """

    def __init__(self, tamanho):
        self.tamanho = tamanho
        self._dados = array("f", [0.0] * tamanho)
        self._proximo = 0      # posição onde entra a próxima leitura
        self.quantidade = 0

    def adicionar(self, valor):
        self._dados[self._proximo] = valor
        self._proximo = (self._proximo + 1) % self.tamanho
        if self.quantidade < self.tamanho:
            self.quantidade += 1

    def __len__(self):
        return self.quantidade

    def valores(self):
        """Percorre as leituras da mais antiga para a mais nova."""
        inicio = (self._proximo - self.quantidade) % self.tamanho
        for i in range(self.quantidade):
            yield self._dados[(inicio + i) % self.tamanho]


class AlarmeCalor:
    """Decide quando o buzzer deve tocar.

    Liga quando a temperatura passa de LIMITE_QUENTE (32 °C) e só desliga
    quando ela cai abaixo de 32 - HISTERESE. Sem essa folga, uma leitura
    oscilando entre 31,99 e 32,01 ligaria e desligaria o alarme sem parar.
    O botão B alterna: o 1º toque silencia, o 2º faz voltar a tocar.
    Se a temperatura cair e subir de novo, o alarme volta sempre tocando.
    """

    HISTERESE = 0.5

    def __init__(self, limite=LIMITE_QUENTE):
        self.limite = limite
        self.ativo = False
        self.silenciado = False

    def atualizar(self, t):
        if not self.ativo and t > self.limite:
            self.ativo = True
            self.silenciado = False
        elif self.ativo and t < self.limite - self.HISTERESE:
            self.ativo = False
            self.silenciado = False

    def alternar_silencio(self):
        """Silencia ou reativa o alarme.

        Devolve "silenciado", "reativado" ou None (sem alarme para mexer).
        """
        if not self.ativo:
            return None
        self.silenciado = not self.silenciado
        return "silenciado" if self.silenciado else "reativado"

    @property
    def tocando(self):
        return self.ativo and not self.silenciado


class MinMax:
    """Menor e maior valor desde o último zerar()."""

    def __init__(self):
        self.zerar()

    def zerar(self):
        self.minimo = None
        self.maximo = None

    def adicionar(self, valor):
        if self.minimo is None or valor < self.minimo:
            self.minimo = valor
        if self.maximo is None or valor > self.maximo:
            self.maximo = valor

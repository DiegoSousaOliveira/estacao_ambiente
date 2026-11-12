"""
lib/display.py - Telas da Estação Ambiente no OLED SSD1306 (128x64).

Quatro telas, trocadas pelo botão A (ou pelo app do PC):
  0. principal   -> temperatura em dígitos grandes, umidade e faixa de conforto
  1. histórico   -> curva das últimas 60 leituras, com escala automática
  2. informações -> IP, mínimas/máximas do dia e tempo ligado
  3. Wi-Fi       -> situação da conexão, nome da rede, IP e força do sinal

Toda tela segue o mesmo esqueleto, para o visual ficar consistente:
  - barra de título invertida (fundo aceso, letras apagadas) no topo,
    com bolinhas à direita indicando em qual tela estamos;
  - margem de 2 px nas bordas;
  - separadores finos de 1 px entre os blocos.

Coordenadas: x vai de 0 (esquerda) a 127, y de 0 (topo) a 63.
A fonte embutida do framebuf tem 8x8 px e só caracteres ASCII (sem acento).
"""

import framebuf

from medidas import NOMES_FAIXA_TEMP, NOMES_FAIXA_UMID
from ssd1306 import SSD1306_I2C

# ---------------------------------------------------------------------------
# Geometria geral
# ---------------------------------------------------------------------------
LARGURA = 128
ALTURA = 64
ENDERECO_OLED = 0x3C

MARGEM = 2
FONTE = 8                  # a fonte do framebuf é 8x8
ALTURA_BARRA = 10          # barra de título: y de 0 a 9
X_TITULO = 3
Y_TITULO = 1

ACESO = 1
APAGADO = 0

TELA_PRINCIPAL = 0
TELA_GRAFICO = 1
TELA_INFO = 2
TELA_WIFI = 3
TITULOS = ("AMBIENTE", "HISTORICO", "INFO", "WI-FI")
NUM_TELAS = len(TITULOS)

# ---------------------------------------------------------------------------
# Dígitos grandes (estilo display de 7 segmentos)
# ---------------------------------------------------------------------------
DIG_LARGURA = 12
DIG_ALTURA = 20
DIG_TRACO = 3              # espessura de cada segmento
DIG_ESPACO = 3             # espaço entre dígitos

# ---------------------------------------------------------------------------
# Posições da tela principal
# ---------------------------------------------------------------------------
X_VALOR = 22               # coluna dos valores, logo depois dos ícones de 16 px
Y_TEMP = 13                # topo dos dígitos grandes (vão até y = 32)
Y_FAIXA_TEMP = 35
Y_SEPARADOR = 44
Y_UMID_ICONE = 46          # o ícone de 16 px termina em y = 61 (margem de 2 px)
Y_UMID_TEXTO = 50          # texto centralizado verticalmente com o ícone

# ---------------------------------------------------------------------------
# Área do gráfico
# ---------------------------------------------------------------------------
GRAF_ESQ = 37              # deixa espaço para os rótulos "33.5" à esquerda
GRAF_DIR = LARGURA - MARGEM - 1
GRAF_TOPO = 16
GRAF_BASE = 57
# Escala mínima de 1 °C: sem ela, um ruído de 0,02 °C ocuparia a tela
# inteira e pareceria uma grande variação.
AMPLITUDE_MINIMA = 1.0

# ---------------------------------------------------------------------------
# Posições da tela de informações
# ---------------------------------------------------------------------------
Y_IP = 13
Y_SEP_1 = 24
Y_LINHA_TEMP = 28
Y_LINHA_UMID = 39
Y_SEP_2 = 50
Y_LIGADO = 54
X_SETA_MIN = 24
X_SETA_MAX = 76

# ---------------------------------------------------------------------------
# Posições da tela de Wi-Fi
# ---------------------------------------------------------------------------
Y_WIFI_ESTADO = 13
Y_WIFI_SEP_1 = 24
Y_WIFI_REDE = 28
Y_WIFI_IP = 39
Y_WIFI_SEP_2 = 50
Y_WIFI_DICA = 54
X_BARRAS_SINAL = 113       # 4 barrinhas de sinal no canto direito
MAX_CARACTERES = (LARGURA - 2 * MARGEM) // FONTE   # 15 letras por linha


# ---------------------------------------------------------------------------
# Ícones desenhados à mão: "#" = pixel aceso, "." = apagado.
# Dá para editar o desenho direto aqui e ver o resultado no OLED.
# ---------------------------------------------------------------------------
_DESENHO_TERMOMETRO = (
    "......####......",
    ".....#....#.....",
    ".....#....#.##..",
    ".....#....#.....",
    ".....#.##.#.##..",
    ".....#.##.#.....",
    ".....#.##.#.##..",
    ".....#.##.#.....",
    ".....#.##.#.....",
    "....#..##..#....",
    "...#.######.#...",
    "...#.######.#...",
    "...#.######.#...",
    "...#..####..#...",
    "....#......#....",
    ".....######.....",
)

_DESENHO_GOTA = (
    ".......##.......",
    ".......##.......",
    "......#..#......",
    "......#..#......",
    ".....#....#.....",
    ".....#....#.....",
    "....#......#....",
    "....#......#....",
    "...#........#...",
    "...#.##.....#...",
    "...#.#......#...",
    "...#.#......#...",
    "...#..#.....#...",
    "....#......#....",
    ".....#....#.....",
    "......####......",
)

_DESENHO_WIFI = (
    "..####..",
    ".#....#.",
    "#......#",
    "..####..",
    ".#....#.",
    "........",
    "...##...",
    "...##...",
)

_DESENHO_SETA_BAIXO = (
    "........",
    "........",
    "#######.",
    ".#####..",
    "..###...",
    "...#....",
    "........",
    "........",
)

_DESENHO_SETA_CIMA = (
    "........",
    "........",
    "...#....",
    "..###...",
    ".#####..",
    "#######.",
    "........",
    "........",
)


# Alto-falante na barra de título: com ondas = alarme tocando; com X = silenciado
_DESENHO_SOM = (
    "...#..#.",
    "..##...#",
    "####.#.#",
    "####.#.#",
    "####.#.#",
    "..##...#",
    "...#..#.",
    "........",
)

_DESENHO_MUDO = (
    "...#....",
    "..##....",
    "####.#.#",
    "####..#.",
    "####.#.#",
    "..##....",
    "...#....",
    "........",
)


def _bitmap(linhas):
    """Converte o desenho em texto num FrameBuffer pronto para blit().

    Formato MONO_HLSB: cada byte guarda 8 pixels de uma linha, e o bit
    mais à esquerda é o de maior valor (0x80).
    """
    largura = len(linhas[0])
    altura = len(linhas)
    bytes_por_linha = (largura + 7) // 8
    buf = bytearray(bytes_por_linha * altura)
    for y, linha in enumerate(linhas):
        for x, caractere in enumerate(linha):
            if caractere == "#":
                buf[y * bytes_por_linha + x // 8] |= 0x80 >> (x % 8)
    return framebuf.FrameBuffer(buf, largura, altura, framebuf.MONO_HLSB)


ICONE_TERMOMETRO = _bitmap(_DESENHO_TERMOMETRO)
ICONE_GOTA = _bitmap(_DESENHO_GOTA)
ICONE_WIFI = _bitmap(_DESENHO_WIFI)
SETA_BAIXO = _bitmap(_DESENHO_SETA_BAIXO)
SETA_CIMA = _bitmap(_DESENHO_SETA_CIMA)
ICONE_SOM = _bitmap(_DESENHO_SOM)
ICONE_MUDO = _bitmap(_DESENHO_MUDO)

# Os desenhos em texto já viraram bitmaps; apagá-los devolve a RAM.
del _DESENHO_TERMOMETRO, _DESENHO_GOTA, _DESENHO_WIFI
del _DESENHO_SETA_BAIXO, _DESENHO_SETA_CIMA, _DESENHO_SOM, _DESENHO_MUDO

# O ícone do alarme fica entre o título (termina em x = 75 no maior,
# "HISTORICO") e as bolinhas de página (com 4 telas, começam em x = 97).
X_ICONE_ALARME = 86
Y_ICONE_ALARME = 1
ESPACO_BOLINHAS = 8        # distância entre os centros das bolinhas de página


# ---------------------------------------------------------------------------
# Segmentos de cada dígito
#
#      aaaa
#     f    b
#     f    b
#      gggg
#     e    c
#     e    c
#      dddd
# ---------------------------------------------------------------------------
_A, _B, _C, _D, _E, _F, _G = 64, 32, 16, 8, 4, 2, 1
SEGMENTOS = {
    "0": _A | _B | _C | _D | _E | _F,
    "1": _B | _C,
    "2": _A | _B | _G | _E | _D,
    "3": _A | _B | _G | _C | _D,
    "4": _F | _G | _B | _C,
    "5": _A | _F | _G | _C | _D,
    "6": _A | _F | _G | _E | _C | _D,
    "7": _A | _B | _C,
    "8": _A | _B | _C | _D | _E | _F | _G,
    "9": _A | _B | _C | _D | _F | _G,
    "-": _G,
}


class Display:
    def __init__(self, i2c, endereco=ENDERECO_OLED):
        self.tela = TELA_PRINCIPAL
        self._avisou_erro = False
        try:
            self.oled = SSD1306_I2C(LARGURA, ALTURA, i2c, endereco)
            self.fb = self.oled
            self.buffer = self.oled.buffer
            self.ok = True
        except OSError:
            # Sem OLED a estação continua funcionando (terminal, LEDs, web).
            # Desenhamos num buffer "de mentira" para não precisar testar
            # self.ok em cada função de desenho.
            self.oled = None
            self.buffer = bytearray(LARGURA * ALTURA // 8)
            self.fb = framebuf.FrameBuffer(self.buffer, LARGURA, ALTURA,
                                           framebuf.MONO_VLSB)
            self.ok = False
        # Função chamada com a imagem pronta sempre que a tela muda.
        # O main.py liga aqui o envio para o app do PC (espelho do OLED).
        self.espelho = None

    # -----------------------------------------------------------------------
    # API usada pelo main.py
    # -----------------------------------------------------------------------
    def proxima_tela(self):
        self.tela = (self.tela + 1) % NUM_TELAS

    def desenhar(self, estado):
        """Redesenha a tela atual com os dados do estado e envia ao OLED."""
        self.fb.fill(APAGADO)
        self._barra(TITULOS[self.tela], self.tela)
        if estado.alarme.ativo:
            icone = ICONE_MUDO if estado.alarme.silenciado else ICONE_SOM
            self._icone_invertido(icone, X_ICONE_ALARME, Y_ICONE_ALARME)
        if self.tela == TELA_PRINCIPAL:
            self._tela_principal(estado)
        elif self.tela == TELA_GRAFICO:
            self._tela_grafico(estado)
        elif self.tela == TELA_INFO:
            self._tela_info(estado)
        else:
            self._tela_wifi(estado.rede)
        self._mostrar()

    def tela_scan(self, conectores, onde_achou):
        """Mostra o resultado do i2c.scan() na partida.

        conectores: lista de (nome_curto, lista_de_enderecos)
        onde_achou: nome do conector com o AHT10, ou None se não achou.
        """
        fb = self.fb
        fb.fill(APAGADO)
        if onde_achou:
            self._barra("INICIANDO")
            y = 14
            for nome, enderecos in conectores:
                fb.text("%s: %s" % (nome, _lista_enderecos(enderecos)), MARGEM, y)
                y += 10
            self._separador(36)
            self._texto_centro("AHT10 OK (%s)" % onde_achou, 44)
        else:
            self._barra("ERRO: SEM AHT10")
            y = 13
            for nome, enderecos in conectores:
                fb.text("%s: %s" % (nome, _lista_enderecos(enderecos)), MARGEM, y)
                y += 10
            self._separador(34)
            fb.text("CONFIRA OS FIOS", MARGEM, 38)
            fb.text("SDA=GP0 SCL=GP1", MARGEM, 48)
        self._mostrar()

    # -----------------------------------------------------------------------
    # Telas
    # -----------------------------------------------------------------------
    def _tela_principal(self, e):
        fb = self.fb
        valido = e.sensor_ok and e.temperatura is not None

        # Linha da temperatura: ícone + número grande + "°C" no alto
        fb.blit(ICONE_TERMOMETRO, MARGEM, Y_TEMP + 2, APAGADO)
        texto = ("%.1f" % e.temperatura) if valido else "--.-"
        x = self._numero_grande(texto, X_VALOR, Y_TEMP)
        fb.ellipse(x + 5, Y_TEMP + 2, 2, 2, ACESO)  # bolinha do grau
        fb.text("C", x + 9, Y_TEMP)

        faixa = NOMES_FAIXA_TEMP[e.faixa_temp] if valido else "SEM LEITURA"
        fb.text(faixa, X_VALOR, Y_FAIXA_TEMP)

        self._separador(Y_SEPARADOR)

        # Linha da umidade: gota + valor + faixa alinhada à direita
        fb.blit(ICONE_GOTA, MARGEM, Y_UMID_ICONE, APAGADO)
        if valido:
            fb.text("%d%%" % round(e.umidade), X_VALOR, Y_UMID_TEXTO)
            self._texto_direita(NOMES_FAIXA_UMID[e.faixa_umid], Y_UMID_TEXTO)
        else:
            fb.text("--%", X_VALOR, Y_UMID_TEXTO)

    def _tela_grafico(self, e):
        fb = self.fb
        hist = e.historico
        n = len(hist)
        if n < 2:
            self._texto_centro("COLETANDO...", 28)
            self._texto_centro("%d/%d" % (n, hist.tamanho), 40)
            return

        # Escala automática: o menor valor fica na base e o maior no topo
        menor = maior = None
        for v in hist.valores():
            if menor is None or v < menor:
                menor = v
            if maior is None or v > maior:
                maior = v
        if maior - menor < AMPLITUDE_MINIMA:
            meio = (maior + menor) / 2
            menor = meio - AMPLITUDE_MINIMA / 2
            maior = meio + AMPLITUDE_MINIMA / 2

        # Rótulos alinhados com as linhas guia de cima e de baixo
        fb.text("%.1f" % maior, MARGEM, GRAF_TOPO - 3)
        fb.text("%.1f" % menor, MARGEM, GRAF_BASE - 3)
        for y in (GRAF_TOPO, (GRAF_TOPO + GRAF_BASE) // 2, GRAF_BASE):
            for x in range(GRAF_ESQ, GRAF_DIR + 1, 3):  # linha pontilhada
                fb.pixel(x, y, ACESO)

        # A leitura mais nova fica sempre na borda direita; enquanto o
        # histórico não enche, a curva "cresce" da direita para a esquerda.
        escala_y = (GRAF_BASE - GRAF_TOPO) / (maior - menor)
        passo_x = (GRAF_DIR - GRAF_ESQ) / (hist.tamanho - 1)
        primeira_posicao = hist.tamanho - n
        x_ant = y_ant = None
        for i, v in enumerate(hist.valores()):
            x = int(GRAF_ESQ + (primeira_posicao + i) * passo_x + 0.5)
            y = int(GRAF_BASE - (v - menor) * escala_y + 0.5)
            if x_ant is not None:
                fb.line(x_ant, y_ant, x, y, ACESO)
            x_ant, y_ant = x, y
        fb.ellipse(x_ant, y_ant, 2, 2, ACESO, True)  # destaca a leitura atual

    def _tela_info(self, e):
        fb = self.fb

        # IP da placa (um IP longo, com 15 caracteres, não deixa espaço para o ícone)
        if e.ip:
            if len(e.ip) <= 14:
                fb.blit(ICONE_WIFI, MARGEM, Y_IP, APAGADO)
                fb.text(e.ip, MARGEM + 11, Y_IP)
            else:
                fb.text(e.ip, MARGEM, Y_IP)
        else:
            fb.blit(ICONE_WIFI, MARGEM, Y_IP, APAGADO)
            fb.text("SEM WI-FI", MARGEM + 11, Y_IP)

        self._separador(Y_SEP_1)

        # Mínimas e máximas do dia
        self._linha_min_max("T", e.temp_minmax.minimo, e.temp_minmax.maximo,
                            _formatar_temp, Y_LINHA_TEMP)
        self._linha_min_max("UR", e.umid_minmax.minimo, e.umid_minmax.maximo,
                            _formatar_umid, Y_LINHA_UMID)

        self._separador(Y_SEP_2)
        fb.text("LIGADO " + _formatar_tempo(e.segundos_ligado()), MARGEM, Y_LIGADO)

    def _tela_wifi(self, rede):
        fb = self.fb
        fb.blit(ICONE_WIFI, MARGEM, Y_WIFI_ESTADO, APAGADO)
        fb.text(rede.nome_estado, MARGEM + 11, Y_WIFI_ESTADO)
        if rede.conectado:
            self._barras_sinal(rede.rssi)

        self._separador(Y_WIFI_SEP_1)
        self._texto_centro(_cortar(rede.ssid or "NENHUMA REDE"), Y_WIFI_REDE)
        self._texto_centro(rede.ip or "SEM IP", Y_WIFI_IP)
        self._separador(Y_WIFI_SEP_2)

        if rede.conectado and rede.rssi is not None:
            self._texto_centro("SINAL %d dBm" % rede.rssi, Y_WIFI_DICA)
        else:
            self._texto_centro(rede.dica, Y_WIFI_DICA)

    def _barras_sinal(self, rssi):
        """Quatro barrinhas de altura crescente, como no celular."""
        if rssi is None:
            return
        if rssi > -55:
            cheias = 4
        elif rssi > -67:
            cheias = 3
        elif rssi > -78:
            cheias = 2
        else:
            cheias = 1
        base = Y_WIFI_ESTADO + 7
        for i in range(4):
            altura = 2 + 2 * i
            x = X_BARRAS_SINAL + i * 3
            if i < cheias:
                self.fb.fill_rect(x, base - altura + 1, 2, altura, ACESO)
            else:
                self.fb.pixel(x, base, ACESO)  # barra vazia: só um pontinho

    # -----------------------------------------------------------------------
    # Peças de desenho reaproveitadas
    # -----------------------------------------------------------------------
    def _barra(self, titulo, pagina=None):
        fb = self.fb
        fb.fill_rect(0, 0, LARGURA, ALTURA_BARRA, ACESO)
        fb.text(titulo, X_TITULO, Y_TITULO, APAGADO)
        if pagina is None:
            return
        # Bolinhas de página: a cheia é a tela atual
        for i in range(NUM_TELAS):
            cx = LARGURA - 5 - (NUM_TELAS - 1 - i) * ESPACO_BOLINHAS
            fb.ellipse(cx, 4, 2, 2, APAGADO, i == pagina)

    def _icone_invertido(self, icone, x, y):
        """Desenha o ícone com pixels APAGADOS, para aparecer sobre a barra acesa.

        O blit() copia os pixels acesos como acesos; aqui precisamos do
        contrário, então percorremos o ícone pixel a pixel (são só 64).
        """
        for j in range(8):
            for i in range(8):
                if icone.pixel(i, j):
                    self.fb.pixel(x + i, y + j, APAGADO)

    def _separador(self, y):
        self.fb.hline(MARGEM, y, LARGURA - 2 * MARGEM, ACESO)

    def _texto_centro(self, texto, y):
        self.fb.text(texto, (LARGURA - len(texto) * FONTE) // 2, y)

    def _texto_direita(self, texto, y):
        self.fb.text(texto, LARGURA - MARGEM - len(texto) * FONTE, y)

    def _linha_min_max(self, rotulo, minimo, maximo, formatar, y):
        fb = self.fb
        fb.text(rotulo, MARGEM, y)
        fb.blit(SETA_BAIXO, X_SETA_MIN, y, APAGADO)
        fb.text(formatar(minimo), X_SETA_MIN + 10, y)
        fb.blit(SETA_CIMA, X_SETA_MAX, y, APAGADO)
        fb.text(formatar(maximo), X_SETA_MAX + 10, y)

    def _numero_grande(self, texto, x, y):
        """Desenha o texto com dígitos grandes. Devolve o x onde terminou."""
        for caractere in texto:
            if caractere == ".":
                self.fb.fill_rect(x, y + DIG_ALTURA - DIG_TRACO,
                                  DIG_TRACO, DIG_TRACO, ACESO)
                x += DIG_TRACO + DIG_ESPACO
            else:
                self._digito(x, y, SEGMENTOS.get(caractere, 0))
                x += DIG_LARGURA + DIG_ESPACO
        return x - DIG_ESPACO

    def _digito(self, x, y, seg):
        fb = self.fb
        w, h, t = DIG_LARGURA, DIG_ALTURA, DIG_TRACO
        meio = y + (h - t) // 2           # topo do segmento do meio (g)
        alt_cima = meio - y + t           # altura das barras verticais de cima
        alt_baixo = y + h - meio          # altura das barras verticais de baixo
        if seg & _A:
            fb.fill_rect(x, y, w, t, ACESO)
        if seg & _G:
            fb.fill_rect(x, meio, w, t, ACESO)
        if seg & _D:
            fb.fill_rect(x, y + h - t, w, t, ACESO)
        if seg & _F:
            fb.fill_rect(x, y, t, alt_cima, ACESO)
        if seg & _B:
            fb.fill_rect(x + w - t, y, t, alt_cima, ACESO)
        if seg & _E:
            fb.fill_rect(x, meio, t, alt_baixo, ACESO)
        if seg & _C:
            fb.fill_rect(x + w - t, meio, t, alt_baixo, ACESO)
        # Apagar os 4 cantos arredonda o dígito e deixa o visual menos "quadradão"
        for cx, cy in ((x, y), (x + w - 1, y), (x, y + h - 1), (x + w - 1, y + h - 1)):
            fb.pixel(cx, cy, APAGADO)

    def _mostrar(self):
        if self.espelho:
            self.espelho(self.buffer)
        if not self.ok:
            return
        try:
            self.oled.show()
            self._avisou_erro = False
        except OSError as erro:
            # Um mau contato no OLED não pode derrubar a estação.
            if not self._avisou_erro:
                print("[AVISO] falha ao atualizar o OLED:", erro)
                self._avisou_erro = True


# ---------------------------------------------------------------------------
# Formatação de valores
# ---------------------------------------------------------------------------
def _lista_enderecos(enderecos):
    """[0x38, 0x6B] -> "38 6B" (cabe no máximo 3 endereços na linha)."""
    if not enderecos:
        return "-"
    texto = " ".join("%02X" % e for e in enderecos[:3])
    return texto + "+" if len(enderecos) > 3 else texto


def _cortar(texto):
    """Corta textos maiores que a linha do OLED, marcando com "..". """
    if len(texto) <= MAX_CARACTERES:
        return texto
    return texto[:MAX_CARACTERES - 2] + ".."


def _formatar_temp(valor):
    return "--.-" if valor is None else "%.1f" % valor


def _formatar_umid(valor):
    return "--" if valor is None else "%d%%" % round(valor)


def _formatar_tempo(segundos):
    """Segundos -> "01:23:45", ou "2d 03:15" depois de um dia."""
    dias, resto = divmod(int(segundos), 86400)
    horas, resto = divmod(resto, 3600)
    minutos, segundos = divmod(resto, 60)
    if dias:
        return "%dd %02d:%02d" % (dias, horas, minutos)
    return "%02d:%02d:%02d" % (horas, minutos, segundos)

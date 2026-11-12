"""
app_pc/estacao_pc.py - App de computador da Estação Ambiente.

Conversa com a BitDogLab pelo cabo USB (a mesma porta do terminal do
MicroPico) e serve para a apresentação:

  - espelha o OLED da placa em tamanho grande, pixel por pixel, para a
    plateia acompanhar pelo monitor ou projetor;
  - mostra as leituras em cartões (temperatura, umidade, conforto);
  - "aperta" os botões A e B à distância;
  - troca a rede e a senha do Wi-Fi da placa e mostra o IP dela;
  - tela cheia (botão no topo ou F11; Esc sai) para projetor.

Como usar:
    pip install -r requirements.txt
    python estacao_pc.py              (procura a placa sozinho)
    python estacao_pc.py --porta COM8

Só um programa por vez pode abrir a porta USB: desconecte o terminal do
MicroPico antes de abrir o app (e vice-versa).

O protocolo (linhas de texto começando com "@") está explicado em
lib/ponte_usb.py, do lado da placa.
"""

import argparse
import base64
import binascii
import collections
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    raise SystemExit("Falta o pyserial. Instale com:  pip install -r requirements.txt")

# ---------------------------------------------------------------------------
# Comunicação
# ---------------------------------------------------------------------------
VID_RASPBERRY_PI = 0x2E8A        # identificador USB das placas Raspberry Pi
VELOCIDADE = 115200              # no USB do Pico o valor é ignorado, mas o pyserial exige
INTERVALO_OLA_MS = 2000          # "ainda estou aqui" para a placa (ela desiste após 6 s)
ESPERA_RESPOSTA_S = 6            # sem resposta nesse tempo = placa parada no REPL?
INTERVALO_FILA_MS = 30           # de quanto em quanto tempo a janela lê as mensagens
LINHAS_MAX_LOG = 400
# Só estas mensagens provam que o main.py está rodando. Qualquer outra
# linha com "@" é o REPL repetindo (eco) o que o app enviou.
TIPOS_DA_PLACA = ("@TELA", "@DADOS", "@WIFI", "@REDES", "@RESP")

# ---------------------------------------------------------------------------
# Visual (mesma paleta do painel web)
# ---------------------------------------------------------------------------
FUNDO = "#0F172A"
CARTAO = "#1E293B"
BORDA = "#334155"
TEXTO = "#F1F5F9"
TEXTO_2 = "#94A3B8"
COR_TEMP = "#FB923C"
COR_UMID = "#38BDF8"
COR_CONFORTO = "#2DD4BF"
COR_AVISO = "#FBBF24"
COR_ERRO = "#F87171"
BOTAO = "#334155"
BOTAO_ATIVO = "#475569"

OLED_LARGURA, OLED_ALTURA = 128, 64
OLED_ACESO = "#E0F2FE"           # branco-azulado, como o OLED de verdade
OLED_APAGADO = "#0B1222"
OLED_VIDRO = "#020617"

RAIO_CARTAO = 16
ESPACO = 16

FAIXAS_TEMP = {"FRIO": "Frio", "CONFORTAVEL": "Confortável",
               "QUENTE": "Quente", "MUITO QUENTE": "Muito quente"}
FAIXAS_UMID = {"BAIXA": "baixa", "MODERADA": "moderada",
               "IDEAL": "ideal", "ALTA": "alta"}
ESTADOS_WIFI = {
    "SEM CONFIG": ("Nenhuma rede configurada", TEXTO_2),
    "CONECTANDO": ("Conectando…", COR_AVISO),
    "CONECTADO": ("Conectado", COR_CONFORTO),
    "SENHA ERRADA": ("Senha incorreta", COR_ERRO),
    "REDE AUSENTE": ("Rede não encontrada", COR_ERRO),
    "FALHOU": ("Falha ao conectar — tentando de novo", COR_ERRO),
}


def numero(valor, casas=1):
    """33.25 -> "33,3" (vírgula decimal, como se escreve no Brasil)."""
    if valor is None:
        return "--"
    return f"{valor:.{casas}f}".replace(".", ",")


def tempo_ligado(segundos):
    dias, resto = divmod(int(segundos), 86400)
    horas, resto = divmod(resto, 3600)
    minutos, segundos = divmod(resto, 60)
    texto = f"{horas:02d}:{minutos:02d}:{segundos:02d}"
    return f"{dias}d {texto}" if dias else texto


def procurar_portas():
    """Lista (porta, descrição), com as placas Raspberry Pi primeiro."""
    portas = list(list_ports.comports())
    portas.sort(key=lambda p: p.vid != VID_RASPBERRY_PI)
    return [(p.device, "BitDogLab" if p.vid == VID_RASPBERRY_PI else p.description)
            for p in portas]


# ---------------------------------------------------------------------------
# Ligação serial (roda numa thread separada para a janela não travar)
# ---------------------------------------------------------------------------
class Ligacao:
    def __init__(self, porta, fila):
        self.porta = porta
        self.fila = fila
        self.serial = serial.Serial(porta, VELOCIDADE, timeout=0.1)
        self._ativa = True
        self._trava = threading.Lock()
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self):
        pendente = b""
        while self._ativa:
            try:
                dados = self.serial.read(4096)
            except (serial.SerialException, OSError) as erro:
                if self._ativa:
                    self.fila.put(("caiu", str(erro)))
                return
            if not dados:
                continue
            pendente += dados
            *linhas, pendente = pendente.split(b"\n")
            for linha in linhas:
                self.fila.put(("linha", linha.decode("utf-8", "replace").rstrip("\r")))

    def enviar(self, comando):
        # ensure_ascii (padrão) troca acentos por ç etc.: a placa lê um
        # caractere por vez e assim nunca recebe metade de uma letra.
        dados = ("@" + json.dumps(comando) + "\n").encode("ascii")
        with self._trava:
            self.serial.write(dados)

    def reiniciar_placa(self):
        # Ctrl+C para o programa e Ctrl+D faz o "soft reset", que roda o main.py
        with self._trava:
            self.serial.write(b"\x03\x03")
            time.sleep(0.2)
            self.serial.write(b"\x04")

    def fechar(self):
        self._ativa = False
        try:
            self.serial.close()
        except (serial.SerialException, OSError):
            pass


# ---------------------------------------------------------------------------
# Peças visuais
# ---------------------------------------------------------------------------
class Cartao(tk.Canvas):
    """Cartão de cantos arredondados com um Frame (self.interior) dentro."""

    def __init__(self, pai, largura, altura, escala, titulo=None, fontes=None):
        super().__init__(pai, width=largura, height=altura, bg=FUNDO,
                         highlightthickness=0, bd=0)
        r = int(RAIO_CARTAO * escala)
        d = 2 * r
        # Quatro círculos nos cantos + dois retângulos = retângulo arredondado
        for x, y in ((0, 0), (largura - d, 0), (0, altura - d), (largura - d, altura - d)):
            self.create_oval(x, y, x + d, y + d, fill=CARTAO, outline=CARTAO)
        self.create_rectangle(r, 0, largura - r, altura, fill=CARTAO, outline=CARTAO)
        self.create_rectangle(0, r, largura, altura - r, fill=CARTAO, outline=CARTAO)

        pad = int(ESPACO * escala)
        self.interior = tk.Frame(self, bg=CARTAO)
        self.create_window(pad, pad, window=self.interior, anchor="nw",
                           width=largura - 2 * pad, height=altura - 2 * pad)
        if titulo:
            tk.Label(self.interior, text=titulo.upper(), bg=CARTAO, fg=TEXTO_2,
                     font=fontes["rotulo"]).pack(anchor="w")


def botao(pai, texto, comando, fonte, primario=False):
    fundo = COR_CONFORTO if primario else BOTAO
    return tk.Button(pai, text=texto, command=comando, font=fonte,
                     bg=fundo, fg=FUNDO if primario else TEXTO,
                     activebackground="#5EEAD4" if primario else BOTAO_ATIVO,
                     activeforeground=FUNDO if primario else TEXTO,
                     disabledforeground=TEXTO_2, relief="flat", bd=0,
                     padx=14, pady=6, cursor="hand2")


def campo(pai, fonte, **opcoes):
    return tk.Entry(pai, font=fonte, bg=FUNDO, fg=TEXTO, insertbackground=TEXTO,
                    relief="flat", highlightthickness=1, highlightbackground=BORDA,
                    highlightcolor=COR_CONFORTO, **opcoes)


# ---------------------------------------------------------------------------
# Janela principal
# ---------------------------------------------------------------------------
class App:
    def __init__(self, raiz, porta_inicial=None):
        self.raiz = raiz
        self.fila = queue.Queue()
        self.ligacao = None
        self.ultima_mensagem = None     # hora da última mensagem "@" da placa
        self.conectado_em = None
        self.avisou_sem_resposta = False
        self.placa_no_repl = False      # programa parado no prompt ">>>"
        self.tela_cheia = False
        # O que já chegou da placa fica guardado para a janela poder ser
        # remontada (ao entrar/sair da tela cheia) sem perder nada.
        self._ultimo_quadro = None
        self._ultimos_dados = None
        self._ultimo_wifi = None
        self._status_atual = ("Desconectado", TEXTO_2)
        self._msg_wifi = ("", TEXTO_2)
        self._linhas_log = collections.deque(maxlen=LINHAS_MAX_LOG)
        self._quadro = bytearray(OLED_LARGURA * OLED_ALTURA // 8)

        # Em telas com zoom (125 %, 150 %) tudo é ampliado na mesma proporção
        self.escala = max(1.0, raiz.winfo_fpixels("1i") / 96)
        self._preparar_estilo()
        self._montar_janela()
        self._centralizar()

        self.atualizar_portas(porta_inicial)
        self.raiz.after(INTERVALO_FILA_MS, self._ler_fila)
        self.raiz.after(INTERVALO_OLA_MS, self._dar_ola)
        self.raiz.protocol("WM_DELETE_WINDOW", self._fechar)
        self.raiz.bind("<F11>", self.alternar_tela_cheia)
        self.raiz.bind("<Escape>", self.sair_tela_cheia)
        if self.combo_porta.get():
            self.raiz.after(300, self.conectar)

    def _centralizar(self):
        self.raiz.update_idletasks()
        largura, altura = self.raiz.winfo_width(), self.raiz.winfo_height()
        x = max(0, (self.raiz.winfo_screenwidth() - largura) // 2)
        y = max(0, (self.raiz.winfo_screenheight() - altura) // 2 - self.px(24))  # sobe um pouco por causa da barra de tarefas
        self.raiz.geometry(f"+{x}+{y}")

    def _calcular_pixel(self):
        """Tamanho de cada pixel do OLED no espelho."""
        largura_tela = self.raiz.winfo_screenwidth()
        altura_tela = self.raiz.winfo_screenheight()
        if self.tela_cheia:
            # Sem o terminal sobra espaço: cresce até bater na altura ou na largura
            por_altura = (altura_tela - self.px(320)) // OLED_ALTURA
            por_largura = (largura_tela - self.px(620)) // OLED_LARGURA
            return max(3, min(por_altura, por_largura))
        # Janela normal: 5 px (ampliado pelo zoom do Windows), mas diminui em
        # telas baixas, como projetores 1366x768, para a janela caber inteira.
        cabe = (altura_tela - self.px(470)) // OLED_ALTURA
        return max(3, min(self.px(5), cabe))

    def alternar_tela_cheia(self, _evento=None):
        self.tela_cheia = not self.tela_cheia
        self.raiz.attributes("-fullscreen", self.tela_cheia)
        self._remontar()
        return "break"

    def sair_tela_cheia(self, _evento=None):
        if self.tela_cheia:
            self.alternar_tela_cheia()

    def _remontar(self):
        """Desmonta e monta a janela de novo no tamanho certo, sem perder nada."""
        formulario = (self.combo_ssid.get(), self.combo_ssid["values"],
                      self.campo_senha.get(), self.mostrar_senha.get())
        portas = (self.combo_porta.get(), self.combo_porta["values"])
        self.geral.destroy()
        self._montar_janela()

        self.combo_porta["values"] = portas[1]
        self.combo_porta.set(portas[0])
        self.combo_ssid["values"] = formulario[1]
        self.combo_ssid.set(formulario[0])
        self.campo_senha.insert(0, formulario[2])
        self.mostrar_senha.set(formulario[3])
        self._alternar_senha()
        self.btn_conectar.configure(text="Desconectar" if self.ligacao else "Conectar")

        self._status(*self._status_atual)
        self._mensagem_wifi(*self._msg_wifi)
        if self.log:
            for linha in self._linhas_log:
                self._escrever_log(linha)
        self._quadro = bytearray(len(self._quadro))  # pixels novos estão todos apagados
        if self._ultimo_quadro:
            self._desenhar_oled(self._ultimo_quadro)
        if self._ultimos_dados:
            self._mostrar_dados(self._ultimos_dados)
        if self._ultimo_wifi:
            self._mostrar_wifi(self._ultimo_wifi)
        if not self.tela_cheia:
            self._centralizar()

    def px(self, valor):
        return int(valor * self.escala)

    # -----------------------------------------------------------------------
    # Montagem da interface
    # -----------------------------------------------------------------------
    def _preparar_estilo(self):
        familias = set(tkfont.families(self.raiz))
        # DM Sans se estiver instalada; senão a fonte do sistema (Segoe UI no Windows)
        texto = next((f for f in ("DM Sans", "Segoe UI", "Helvetica") if f in familias), "TkDefaultFont")
        mono = next((f for f in ("Cascadia Mono", "Consolas", "Courier New") if f in familias), "TkFixedFont")
        self.fontes = {
            "titulo": (texto, 18, "bold"),
            "subtitulo": (texto, 10),
            "rotulo": (texto, 9, "bold"),
            "normal": (texto, 10),
            "destaque": (texto, 13, "bold"),
            "valor": (texto, 28, "bold"),
            "ip": (mono, 18, "bold"),
            "mono": (mono, 9),
            "botao": (texto, 10, "bold"),
        }
        estilo = ttk.Style(self.raiz)
        estilo.theme_use("clam")
        estilo.configure("Escuro.TCombobox", fieldbackground=FUNDO, background=BOTAO,
                         foreground=TEXTO, arrowcolor=TEXTO, bordercolor=BORDA,
                         lightcolor=BORDA, darkcolor=BORDA, insertcolor=TEXTO,
                         selectbackground=FUNDO, selectforeground=TEXTO, padding=4)
        estilo.map("Escuro.TCombobox", fieldbackground=[("readonly", FUNDO)],
                   foreground=[("readonly", TEXTO)])
        self.raiz.option_add("*TCombobox*Listbox.background", CARTAO)
        self.raiz.option_add("*TCombobox*Listbox.foreground", TEXTO)
        self.raiz.option_add("*TCombobox*Listbox.selectBackground", BOTAO_ATIVO)
        self.raiz.option_add("*TCombobox*Listbox.font", self.fontes["normal"])

    def _montar_janela(self):
        raiz, f, px = self.raiz, self.fontes, self.px
        raiz.title("Estação Ambiente — BitDogLab")
        raiz.configure(bg=FUNDO)
        raiz.resizable(False, False)

        geral = self.geral = tk.Frame(raiz, bg=FUNDO)
        if self.tela_cheia:
            geral.place(relx=0.5, rely=0.5, anchor="center")  # no meio da tela
        else:
            geral.pack(padx=px(20), pady=px(16))

        # ---- Cabeçalho -----------------------------------------------------
        topo = tk.Frame(geral, bg=FUNDO)
        topo.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, px(12)))
        nome = tk.Frame(topo, bg=FUNDO)  # título com o subtítulo embaixo
        nome.pack(side="left")
        tk.Label(nome, text="Estação Ambiente", bg=FUNDO, fg=TEXTO,
                 font=f["titulo"]).pack(anchor="w")
        tk.Label(nome, text="BitDogLab · sensor AHT10", bg=FUNDO, fg=TEXTO_2,
                 font=f["subtitulo"]).pack(anchor="w")

        texto_cheia = "Sair da tela cheia (Esc)" if self.tela_cheia else "Tela cheia (F11)"
        botao(topo, texto_cheia, self.alternar_tela_cheia, f["botao"]).pack(side="right")
        self.btn_reiniciar = botao(topo, "Reiniciar placa", self.reiniciar_placa, f["botao"])
        self.btn_reiniciar.pack(side="right", padx=(0, px(8)))
        self.btn_conectar = botao(topo, "Conectar", self.alternar_conexao, f["botao"])
        self.btn_conectar.pack(side="right", padx=(px(8), px(8)))
        self.combo_porta = ttk.Combobox(topo, width=16, style="Escuro.TCombobox",
                                        font=f["normal"], state="readonly")
        self.combo_porta.pack(side="right")
        self.combo_porta.bind("<Button-1>", lambda _e: self.atualizar_portas(self.combo_porta.get()))
        self.lbl_status = tk.Label(topo, text="●  Desconectado", bg=FUNDO, fg=TEXTO_2,
                                   font=f["normal"], width=36, anchor="e")
        self.lbl_status.pack(side="right", padx=(0, px(12)))

        # ---- Coluna da esquerda: espelho do OLED + terminal ---------------
        esquerda = tk.Frame(geral, bg=FUNDO)
        esquerda.grid(row=1, column=0, sticky="n")
        self.tam_pixel = self._calcular_pixel()
        largura_oled = OLED_LARGURA * self.tam_pixel
        altura_oled = OLED_ALTURA * self.tam_pixel
        largura_esq = largura_oled + px(2 * ESPACO + 24)

        cartao_oled = Cartao(esquerda, largura_esq, altura_oled + px(2 * ESPACO + 24 + 26 + 50),
                             self.escala, "Display da placa · espelho ao vivo", f)
        cartao_oled.pack()
        vidro = tk.Frame(cartao_oled.interior, bg=OLED_VIDRO, padx=px(12), pady=px(12))
        vidro.pack(pady=(px(8), px(12)))
        self.oled = tk.Canvas(vidro, width=largura_oled, height=altura_oled,
                              bg=OLED_VIDRO, highlightthickness=0)
        self.oled.pack()
        self._criar_pixels()

        botoes = tk.Frame(cartao_oled.interior, bg=CARTAO)
        botoes.pack(fill="x")
        botao(botoes, "A  ·  Trocar tela", lambda: self.enviar({"cmd": "botao", "qual": "A"}),
              f["botao"]).pack(side="left")
        self.btn_b = botao(botoes, "B  ·  Silenciar alarme",
                           lambda: self.enviar({"cmd": "botao", "qual": "B"}), f["botao"])
        self.btn_b.pack(side="left", padx=px(8))
        self.lbl_tela = tk.Label(botoes, text="", bg=CARTAO, fg=TEXTO_2, font=f["normal"])
        self.lbl_tela.pack(side="right")

        # Na tela cheia (modo apresentação) o terminal sai e o espelho cresce
        self.log = None
        if not self.tela_cheia:
            cartao_log = Cartao(esquerda, largura_esq, px(150), self.escala, "Terminal da placa", f)
            cartao_log.pack(pady=(px(ESPACO), 0))
            self.log = tk.Text(cartao_log.interior, height=5, bg=CARTAO, fg=TEXTO_2,
                               font=f["mono"], relief="flat", bd=0, wrap="none",
                               highlightthickness=0, state="disabled")
            self.log.pack(fill="both", expand=True, pady=(px(6), 0))

        # ---- Coluna da direita: leituras e Wi-Fi ---------------------------
        direita = tk.Frame(geral, bg=FUNDO)
        direita.grid(row=1, column=1, sticky="n", padx=(px(ESPACO), 0))
        largura_dir = px(420)
        meia = (largura_dir - px(ESPACO)) // 2

        linha = tk.Frame(direita, bg=FUNDO)
        linha.pack()
        self.cartao_temp = self._cartao_valor(linha, meia, "Temperatura", COR_TEMP)
        self.cartao_temp["cartao"].pack(side="left")
        self.cartao_umid = self._cartao_valor(linha, meia, "Umidade", COR_UMID)
        self.cartao_umid["cartao"].pack(side="left", padx=(px(ESPACO), 0))

        conforto = Cartao(direita, largura_dir, px(150), self.escala, "Conforto", f)
        conforto.pack(pady=(px(ESPACO), 0))
        self.lbl_conforto = tk.Label(conforto.interior, text="--", bg=CARTAO,
                                     fg=COR_CONFORTO, font=f["destaque"])
        self.lbl_conforto.pack(anchor="w", pady=(px(4), 0))
        self.lbl_sensacao = tk.Label(conforto.interior, text="", bg=CARTAO, fg=TEXTO,
                                     font=f["normal"])
        self.lbl_sensacao.pack(anchor="w")
        self.lbl_alarme = tk.Label(conforto.interior, text="", bg=CARTAO, fg=TEXTO_2,
                                   font=f["normal"])
        self.lbl_alarme.pack(anchor="w")
        self.lbl_ligado = tk.Label(conforto.interior, text="", bg=CARTAO, fg=TEXTO_2,
                                   font=f["normal"])
        self.lbl_ligado.pack(anchor="w")

        self._montar_wifi(direita, largura_dir)

    def _cartao_valor(self, pai, largura, titulo, cor):
        f = self.fontes
        cartao = Cartao(pai, largura, self.px(150), self.escala, titulo, f)
        valor = tk.Label(cartao.interior, text="--", bg=CARTAO, fg=cor, font=f["valor"])
        valor.pack(anchor="w")
        faixa = tk.Label(cartao.interior, text="", bg=CARTAO, fg=TEXTO, font=f["normal"])
        faixa.pack(anchor="w")
        extremos = tk.Label(cartao.interior, text="", bg=CARTAO, fg=TEXTO_2, font=f["normal"])
        extremos.pack(anchor="w")
        return {"cartao": cartao, "valor": valor, "faixa": faixa, "extremos": extremos}

    def _montar_wifi(self, pai, largura):
        f, px = self.fontes, self.px
        cartao = Cartao(pai, largura, px(272), self.escala, "Wi-Fi da placa", f)
        cartao.pack(pady=(px(ESPACO), 0))
        dentro = cartao.interior

        situacao = tk.Frame(dentro, bg=CARTAO)
        situacao.pack(fill="x", pady=(px(4), 0))
        self.lbl_wifi = tk.Label(situacao, text="--", bg=CARTAO, fg=TEXTO_2, font=f["normal"])
        self.lbl_wifi.pack(side="left")
        self.lbl_ip = tk.Label(dentro, text="IP: --", bg=CARTAO, fg=TEXTO, font=f["ip"])
        self.lbl_ip.pack(anchor="w", pady=(0, px(8)))

        grade = tk.Frame(dentro, bg=CARTAO)
        grade.pack(fill="x")
        grade.columnconfigure(1, weight=1)
        tk.Label(grade, text="Rede", bg=CARTAO, fg=TEXTO_2, font=f["normal"]).grid(
            row=0, column=0, sticky="w", padx=(0, px(10)))
        self.combo_ssid = ttk.Combobox(grade, style="Escuro.TCombobox", font=f["normal"])
        self.combo_ssid.grid(row=0, column=1, sticky="ew")
        self.btn_procurar = botao(grade, "Procurar", self.procurar_redes, f["botao"])
        self.btn_procurar.grid(row=0, column=2, padx=(px(8), 0))

        tk.Label(grade, text="Senha", bg=CARTAO, fg=TEXTO_2, font=f["normal"]).grid(
            row=1, column=0, sticky="w", pady=(px(8), 0))
        self.campo_senha = campo(grade, f["normal"], show="•")
        self.campo_senha.grid(row=1, column=1, sticky="ew", pady=(px(8), 0), ipady=px(4))
        self.mostrar_senha = tk.BooleanVar(value=False)
        tk.Checkbutton(grade, text="mostrar", variable=self.mostrar_senha,
                       command=self._alternar_senha, bg=CARTAO, fg=TEXTO_2,
                       selectcolor=FUNDO, activebackground=CARTAO,
                       activeforeground=TEXTO, font=f["normal"], bd=0,
                       highlightthickness=0).grid(row=1, column=2, padx=(px(8), 0),
                                                  pady=(px(8), 0), sticky="w")

        self.btn_salvar = botao(dentro, "Salvar na placa e conectar", self.salvar_wifi,
                                f["botao"], primario=True)
        self.btn_salvar.pack(fill="x", pady=(px(12), px(6)))
        self.lbl_msg_wifi = tk.Label(dentro, text="", bg=CARTAO, fg=TEXTO_2,
                                     font=f["normal"], wraplength=largura - px(40),
                                     justify="left")
        self.lbl_msg_wifi.pack(anchor="w")

    def _criar_pixels(self):
        """Um retângulo para cada um dos 8192 pixels do OLED.

        Criados uma vez só; depois só trocamos a cor dos que mudaram,
        o que é bem mais rápido que redesenhar tudo a cada quadro.
        """
        t = self.tam_pixel
        lado = t - 1  # 1 px de vão entre os pixels dá o visual de tela de verdade
        self._pixels = []
        for y in range(OLED_ALTURA):
            for x in range(OLED_LARGURA):
                self._pixels.append(self.oled.create_rectangle(
                    x * t, y * t, x * t + lado, y * t + lado,
                    fill=OLED_APAGADO, outline=""))

    # -----------------------------------------------------------------------
    # Conexão
    # -----------------------------------------------------------------------
    def atualizar_portas(self, preferida=None):
        portas = procurar_portas()
        self._nomes_portas = {f"{p} · {d}": p for p, d in portas}
        self.combo_porta["values"] = list(self._nomes_portas)
        escolha = next((n for n, p in self._nomes_portas.items() if p == preferida or n == preferida),
                       next(iter(self._nomes_portas), ""))
        self.combo_porta.set(escolha)

    def alternar_conexao(self):
        if self.ligacao:
            self.desconectar("Desconectado")
        else:
            self.conectar()

    def conectar(self):
        porta = self._nomes_portas.get(self.combo_porta.get())
        if not porta:
            self._status("Nenhuma placa · confira o cabo USB", COR_ERRO)
            return
        try:
            self.ligacao = Ligacao(porta, self.fila)
        except serial.SerialException as erro:
            ocupada = "Acesso negado" in str(erro) or "PermissionError" in str(erro)
            if ocupada:
                self._status(f"{porta} em uso · feche o MicroPico", COR_ERRO)
                self._registrar(f"[app] A porta {porta} está aberta em outro programa. "
                                "No VS Code, clique em 'Disconnect' do MicroPico e tente de novo.")
            else:
                self._status(f"Não abriu {porta}", COR_ERRO)
                self._registrar(f"[app] {erro}")
            return
        self.conectado_em = time.time()
        self.ultima_mensagem = None
        self.avisou_sem_resposta = False
        self.placa_no_repl = False
        self.btn_conectar.configure(text="Desconectar")
        self._status("Aguardando a placa…", COR_AVISO)
        self.enviar({"cmd": "ola"})

    def desconectar(self, motivo, cor=TEXTO_2):
        if self.ligacao:
            self.ligacao.fechar()
            self.ligacao = None
        self.btn_conectar.configure(text="Conectar")
        self._status(motivo, cor)

    def reiniciar_placa(self):
        if not self.ligacao:
            self.conectar()
        if self.ligacao:
            self._registrar("[app] Reiniciando a placa (Ctrl+C, Ctrl+D)…")
            self.ligacao.reiniciar_placa()
            self.conectado_em = time.time()
            self.ultima_mensagem = None
            self.avisou_sem_resposta = False
            self.placa_no_repl = False
            self._status("Reiniciando a placa…", COR_AVISO)

    def enviar(self, comando):
        if not self.ligacao:
            return False
        try:
            self.ligacao.enviar(comando)
            return True
        except (serial.SerialException, OSError) as erro:
            self.desconectar("Conexão perdida", COR_ERRO)
            self._registrar(f"[app] Conexão perdida: {erro}")
            return False

    def _dar_ola(self):
        # Com a placa no REPL, não mandamos nada: cada "ola" viraria lixo no prompt
        if self.ligacao and not self.placa_no_repl:
            self.enviar({"cmd": "ola"})
            sem_resposta = (self.ultima_mensagem is None and
                            time.time() - self.conectado_em > ESPERA_RESPOSTA_S)
            if sem_resposta and not self.avisou_sem_resposta:
                self.avisou_sem_resposta = True
                self._status("A placa não respondeu", COR_ERRO)
                self._registrar("[app] A placa não respondeu. Se o main.py não estiver "
                                "rodando, clique em 'Reiniciar placa'.")
        self.raiz.after(INTERVALO_OLA_MS, self._dar_ola)

    # -----------------------------------------------------------------------
    # Mensagens que chegam da placa
    # -----------------------------------------------------------------------
    def _ler_fila(self):
        try:
            for _ in range(200):  # limite por rodada para a janela não engasgar
                tipo, conteudo = self.fila.get_nowait()
                if tipo == "linha":
                    self._tratar_linha(conteudo)
                else:
                    self.desconectar("Cabo desconectado?", COR_ERRO)
                    self._registrar(f"[app] Conexão perdida: {conteudo}")
        except queue.Empty:
            pass
        self.raiz.after(INTERVALO_FILA_MS, self._ler_fila)

    def _tratar_linha(self, linha):
        tipo, _, conteudo = linha.partition(" ")
        if tipo not in TIPOS_DA_PLACA:
            if not linha.strip():
                return
            self._registrar(linha)
            # ">>>" e "..." são o prompt do REPL: o main.py não está rodando
            if linha.startswith((">>>", "...")) and not self.placa_no_repl:
                self.placa_no_repl = True
                self._status("Programa parado · clique Reiniciar", COR_AVISO)
                self._registrar("[app] A placa está no REPL (main.py parado). "
                                "Clique em 'Reiniciar placa' para rodar o programa.")
            return

        self.placa_no_repl = False
        if self.ultima_mensagem is None:
            self._status(f"Placa conectada · {self.ligacao.porta}" if self.ligacao else
                         "Placa conectada", COR_CONFORTO)
        self.ultima_mensagem = time.time()

        try:
            if tipo == "@TELA":
                self._desenhar_oled(base64.b64decode(conteudo))
            elif tipo == "@DADOS":
                self._mostrar_dados(json.loads(conteudo))
            elif tipo == "@WIFI":
                self._mostrar_wifi(json.loads(conteudo))
            elif tipo == "@REDES":
                self._mostrar_redes(json.loads(conteudo))
            elif tipo == "@RESP":
                resposta = json.loads(conteudo)
                self._mensagem_wifi(resposta["msg"], COR_CONFORTO if resposta["ok"] else COR_ERRO)
                self._registrar(f"[placa] {resposta['msg']}")
        except (ValueError, KeyError, binascii.Error) as erro:
            self._registrar(f"[app] mensagem com defeito ({tipo}): {erro}")

    def _desenhar_oled(self, quadro):
        if len(quadro) != len(self._quadro):
            return
        # O SSD1306 guarda a tela em "páginas" de 8 linhas: cada byte é uma
        # coluna de 8 pixels, e o bit 0 é o de cima (formato MONO_VLSB).
        antigo = self._quadro
        config = self.oled.itemconfigure
        for i, (novo_byte, velho_byte) in enumerate(zip(quadro, antigo)):
            mudou = novo_byte ^ velho_byte
            if not mudou:
                continue
            pagina, x = divmod(i, OLED_LARGURA)
            for bit in range(8):
                if mudou >> bit & 1:
                    y = pagina * 8 + bit
                    config(self._pixels[y * OLED_LARGURA + x],
                           fill=OLED_ACESO if novo_byte >> bit & 1 else OLED_APAGADO)
        self._quadro = bytearray(quadro)
        self._ultimo_quadro = bytes(quadro)

    def _mostrar_dados(self, d):
        self._ultimos_dados = d
        # O botão B alterna: com o alarme silenciado, ele serve para reativar
        if d.get("alarme") == "silenciado":
            self.btn_b.configure(text="B  ·  Reativar alarme")
        else:
            self.btn_b.configure(text="B  ·  Silenciar alarme")
        nomes_telas = ("Ambiente", "Histórico", "Info", "Wi-Fi")
        tela = d.get("tela")
        if tela is not None and 0 <= tela < len(nomes_telas):
            self.lbl_tela.configure(text=f"Tela {tela + 1} de 4 · {nomes_telas[tela]}")

        if not d.get("ok"):
            for c in (self.cartao_temp, self.cartao_umid):
                c["valor"].configure(text="--")
                c["faixa"].configure(text="sem leitura do sensor")
            self.lbl_conforto.configure(text="Sensor sem leitura", fg=COR_ERRO)
            self.lbl_sensacao.configure(text="Confira o cabo do AHT10.")
            return

        self.cartao_temp["valor"].configure(text=f"{numero(d['t'])} °C")
        self.cartao_temp["faixa"].configure(text=FAIXAS_TEMP.get(d["ft"], d["ft"]))
        self.cartao_temp["extremos"].configure(
            text=f"mín {numero(d['tmin'])} · máx {numero(d['tmax'])} °C")
        self.cartao_umid["valor"].configure(text=f"{numero(d['u'], 0)} %")
        self.cartao_umid["faixa"].configure(text=f"Umidade {FAIXAS_UMID.get(d['fu'], d['fu'])}")
        self.cartao_umid["extremos"].configure(
            text=f"mín {numero(d['umin'], 0)} · máx {numero(d['umax'], 0)} %")

        self.lbl_conforto.configure(
            text=f"{FAIXAS_TEMP.get(d['ft'], d['ft'])} · umidade {FAIXAS_UMID.get(d['fu'], d['fu'])}",
            fg=COR_CONFORTO)
        self.lbl_sensacao.configure(
            text=f"Sensação térmica {numero(d['st'])} °C   ·   Ponto de orvalho {numero(d['po'])} °C")
        alarme = d.get("alarme")
        if alarme == "tocando":
            texto, cor = "Alarme de calor tocando — aperte B para silenciar", COR_TEMP
        elif alarme == "silenciado":
            texto, cor = "Alarme silenciado — aperte B para voltar a tocar", TEXTO_2
        else:
            texto, cor = "Alarme de calor desligado", TEXTO_2
        self.lbl_alarme.configure(text=texto, fg=cor)
        self.lbl_ligado.configure(text=f"Placa ligada há {tempo_ligado(d['ligado'])}")

    def _mostrar_wifi(self, w):
        self._ultimo_wifi = w
        texto, cor = ESTADOS_WIFI.get(w.get("estado"), (w.get("estado") or "--", TEXTO_2))
        if w.get("ssid") and w.get("estado") != "SEM CONFIG":
            texto += f" · {w['ssid']}"
        if w.get("rssi") is not None and w.get("estado") == "CONECTADO":
            texto += f" · sinal {w['rssi']} dBm"
        self.lbl_wifi.configure(text="●  " + texto, fg=cor)
        self.lbl_ip.configure(text=f"IP: {w.get('ip') or '--'}")
        if w.get("ssid") and not self.combo_ssid.get():
            self.combo_ssid.set(w["ssid"])

    def _mostrar_redes(self, redes):
        self.btn_procurar.configure(state="normal", text="Procurar")
        self.combo_ssid["values"] = [r["ssid"] for r in redes]
        if redes:
            self._mensagem_wifi(f"{len(redes)} redes encontradas · escolha no campo Rede",
                                TEXTO_2)
            for r in redes:
                self._registrar(f"[app] rede {r['ssid']}  ({r['rssi']} dBm)")
            if not self.combo_ssid.get():
                self.combo_ssid.set(redes[0]["ssid"])
        else:
            self._mensagem_wifi("Nenhuma rede encontrada.", COR_AVISO)

    # -----------------------------------------------------------------------
    # Ações do Wi-Fi
    # -----------------------------------------------------------------------
    def procurar_redes(self):
        if self.enviar({"cmd": "redes"}):
            self.btn_procurar.configure(state="disabled", text="Procurando…")
            self._mensagem_wifi("A placa está procurando redes (leva uns 3 s)…", TEXTO_2)
            # Se a resposta não vier (placa desconectada), libera o botão
            self.raiz.after(8000, lambda: self.btn_procurar.configure(state="normal", text="Procurar"))
        else:
            self._mensagem_wifi("Conecte a placa primeiro.", COR_ERRO)

    def salvar_wifi(self):
        ssid = self.combo_ssid.get().strip()
        senha = self.campo_senha.get()
        if not ssid:
            self._mensagem_wifi("Escolha ou digite o nome da rede.", COR_ERRO)
            return
        if len(ssid) > 32:
            self._mensagem_wifi("O nome da rede tem no máximo 32 caracteres.", COR_ERRO)
            return
        if senha and not 8 <= len(senha) <= 63:
            self._mensagem_wifi("A senha do Wi-Fi tem de 8 a 63 caracteres "
                                "(deixe vazio para rede aberta).", COR_ERRO)
            return
        if self.enviar({"cmd": "wifi", "ssid": ssid, "senha": senha}):
            self._mensagem_wifi("Enviando para a placa…", TEXTO_2)
        else:
            self._mensagem_wifi("Conecte a placa primeiro.", COR_ERRO)

    def _alternar_senha(self):
        self.campo_senha.configure(show="" if self.mostrar_senha.get() else "•")

    # -----------------------------------------------------------------------
    # Utilidades
    # -----------------------------------------------------------------------
    def _status(self, texto, cor):
        self._status_atual = (texto, cor)
        self.lbl_status.configure(text="●  " + texto, fg=cor)

    def _mensagem_wifi(self, texto, cor):
        self._msg_wifi = (texto, cor)
        self.lbl_msg_wifi.configure(text=texto, fg=cor)

    def _registrar(self, linha):
        self._linhas_log.append(linha)
        if self.log:  # na tela cheia o terminal está escondido
            self._escrever_log(linha)

    def _escrever_log(self, linha):
        self.log.configure(state="normal")
        self.log.insert("end", linha + "\n")
        excesso = int(self.log.index("end-1c").split(".")[0]) - LINHAS_MAX_LOG
        if excesso > 0:
            self.log.delete("1.0", f"{excesso + 1}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _fechar(self):
        if self.ligacao:
            self.ligacao.fechar()
        self.raiz.destroy()


def main():
    parser = argparse.ArgumentParser(description="App de computador da Estação Ambiente")
    parser.add_argument("--porta", help="porta serial da placa (ex.: COM8). "
                        "Sem isso, o app procura a BitDogLab sozinho.")
    args = parser.parse_args()

    try:
        # Janela nítida em telas com zoom do Windows (125 %, 150 %...)
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass

    raiz = tk.Tk()
    App(raiz, args.porta)
    raiz.mainloop()


if __name__ == "__main__":
    main()

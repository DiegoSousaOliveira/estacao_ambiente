# Estação Ambiente

Medidor de **temperatura e umidade** feito com a placa **BitDogLab** (Raspberry Pi Pico W) e o sensor **AHT10**, programado em **MicroPython**.

A estação mostra as leituras de quatro jeitos ao mesmo tempo:

- **Display OLED** da placa, com 4 telas trocadas pelo botão A;
- **Matriz de LEDs 5×5** funcionando como um termômetro de cores;
- **Painel web** que abre no celular, servido pela própria placa via Wi-Fi;
- **App no computador**, ligado pelo cabo USB, que espelha o OLED em tela grande e configura o Wi-Fi.

> Testado num Pico W (RP2040) com MicroPython v1.29.0.

---

## Sumário

1. [O que a estação faz](#1-o-que-a-estação-faz)
2. [Material e ligações](#2-material-e-ligações)
3. [Preparando o computador](#3-preparando-o-computador)
4. [Gravando o firmware MicroPython](#4-gravando-o-firmware-micropython)
5. [Enviando o projeto para a placa](#5-enviando-o-projeto-para-a-placa)
6. [Configurando o Wi-Fi](#6-configurando-o-wi-fi)
7. [Usando a estação](#7-usando-a-estação)
8. [Painel web](#8-painel-web)
9. [App do computador](#9-app-do-computador)
10. [Como o código funciona](#10-como-o-código-funciona)
11. [Dicas para a apresentação](#11-dicas-para-a-apresentação)
12. [Problemas comuns](#12-problemas-comuns)

---

## 1. O que a estação faz

- Lê o AHT10 a cada **2 s** e suaviza os valores com uma **média móvel** das últimas 5 leituras.
- Calcula a **sensação térmica** (índice de calor da NOAA) e o **ponto de orvalho** (fórmula de Magnus).
- Classifica o conforto:| Temperatura      | Faixa        |  | Umidade        | Faixa    |
  | ---------------- | ------------ | - | -------------- | -------- |
  | abaixo de 20 °C | frio         |  | abaixo de 30 % | baixa    |
  | 20 a 27 °C      | confortável |  | 30 a 40 %      | moderada |
  | 27 a 32 °C      | quente       |  | 40 a 60 %      | ideal    |
  | acima de 32 °C  | muito quente |  | 60 a 70 %      | moderada |
  |                  |              |  | acima de 70 %  | alta     |
- Toca um **alarme** (bipe curto a cada 10 s) enquanto a temperatura passar de 32 °C.
- Guarda as **últimas 60 leituras** (2 minutos) na memória, para os gráficos.
- **Não grava nada** na memória flash: os dados são só ao vivo.

---

## 2. Material e ligações

### Material

- Placa **BitDogLab** com Raspberry Pi **Pico W** (ou Pico 2 W, veja a seção 4);
- Sensor **AHT10** numa placa adaptadora (4 pinos: VIN, GND, SCL, SDA);
- Cabo para o conector I2C da BitDogLab;
- Cabo **micro-USB de dados**. Cabo só de carga não serve: o computador não enxerga a placa.

### Ligação do sensor

O AHT10 vai no **conector I2C0 externo** da BitDogLab:

```
  Placa adaptadora AHT10              BitDogLab · conector I2C0 (externo)
  ┌───────────────┐                   ┌──────────────────────┐
  │  VIN  ●───────┼───────────────────┼──● 3V3               │
  │  GND  ●───────┼───────────────────┼──● GND               │
  │  SCL  ●───────┼───────────────────┼──● SCL   (GP1)       │
  │  SDA  ●───────┼───────────────────┼──● SDA   (GP0)       │
  └───────────────┘                   └──────────────────────┘
       endereço I2C: 0x38
```

- Use **3,3 V**, não 5 V.
- **SDA e SCL trocados** é o erro mais comum. Se o sensor não aparecer, inverta os dois fios.
- O programa também procura o sensor no **conector I2C1** (GP2/GP3), então ele pode ir em qualquer um dos dois.
- Outros sensores I2C podem ficar no mesmo cabo, desde que tenham endereços diferentes.

### Tabela de pinos

Confira na documentação da **sua** versão da BitDogLab. Se algum pino for diferente, mude a constante no topo do `main.py`.

| Componente                   | Pino(s)                                                    | Observação                               |
| ---------------------------- | ---------------------------------------------------------- | ------------------------------------------ |
| Sensor AHT10 (conector I2C0) | SDA = **GP0**, SCL = **GP1**                   | I2C de hardware 0, 100 kHz                 |
| Conector I2C1 (alternativo)  | SDA = **GP2**, SCL = **GP3**                   | por software (SoftI2C)                     |
| OLED SSD1306 128×64         | SDA = **GP14**, SCL = **GP15**                 | I2C de hardware 1, 400 kHz, endereço 0x3C |
| Matriz de LEDs 5×5 WS2812   | **GP7**                                              | ligada em zigue-zague                      |
| LED RGB                      | R = **GP13**, G = **GP11**, B = **GP12** | PWM                                        |
| Botão A                     | **GP5**                                              | pull-up interno: solto = 1, apertado = 0   |
| Botão B                     | **GP6**                                              | pull-up interno                            |
| Buzzer                       | **GP21**                                             | PWM, 2 kHz                                 |

> Por que o conector I2C1 usa SoftI2C? O RP2040 tem só dois controladores I2C de hardware, e o I2C1 já está ocupado pelo OLED. O SoftI2C faz o protocolo por software. É mais lento, mas o AHT10 troca poucos bytes, então não faz diferença.

---

## 3. Preparando o computador

1. Instale o **[VS Code](https://code.visualstudio.com/)**.
2. Instale o **Python 3** pelo [python.org](https://www.python.org/downloads/). No Windows, marque **"Add python.exe to PATH"** na instalação. O MicroPico precisa dele, e o app do computador também.
3. No VS Code, abra a aba de extensões (`Ctrl+Shift+X`) e instale a **MicroPico** (autor: *paulober*). Ela também instala a extensão de Python.
4. Abra **a pasta `estacao-ambiente`** no VS Code (*Arquivo → Abrir Pasta*). Abra essa pasta, e não uma acima dela: as configurações do projeto ficam em `estacao-ambiente/.vscode/`.
5. Rode `Ctrl+Shift+P` → **MicroPico: Initialize MicroPico Project**. Isso faz o editor reconhecer `machine`, `network` e outros módulos do MicroPython. O comando acrescenta as configurações dele ao `.vscode/settings.json` do projeto sem apagar as que já estão lá.

---

## 4. Gravando o firmware MicroPython

O firmware é o "sistema" MicroPython que roda na placa. Ele é gravado uma única vez.

### Descubra qual Pico a sua BitDogLab usa

Olhe o chip principal do módulo Pico:

| Escrito no chip  | Placa    | Firmware para baixar                                                                 |
| ---------------- | -------- | ------------------------------------------------------------------------------------ |
| **RP2040** | Pico W   | [micropython.org/download/RPI_PICO_W](https://micropython.org/download/RPI_PICO_W/)   |
| **RP2350** | Pico 2 W | [micropython.org/download/RPI_PICO2_W](https://micropython.org/download/RPI_PICO2_W/) |

Baixe o arquivo `.uf2` da versão mais recente (*Releases*), por exemplo `RPI_PICO_W-20260824-v1.29.0.uf2`.

> Use o firmware com **W** no nome. O firmware do Pico "comum" até roda no Pico W, mas sem Wi-Fi.

### Gravação pelo modo BOOTSEL

1. Desconecte o cabo USB da placa.
2. **Segure o botão BOOTSEL** (o botão branco do módulo Pico).
3. Conecte o cabo USB no computador, ainda segurando o botão, e solte em seguida.
4. Aparece um "pendrive" chamado **RPI-RP2** (ou **RP2350**).
5. Arraste o arquivo `.uf2` para dentro dele.
6. A placa reinicia sozinha e o pendrive some. Pronto!

### Conferindo

No VS Code, clique em **Board Disconnected** na barra inferior (ou `Ctrl+Shift+P` → *MicroPico: Connect*). No terminal da placa (REPL), digite:

```python
>>> import sys; sys.implementation._machine
'Raspberry Pi Pico W with RP2040'
```

Ao rodar, o `main.py` também mostra essa linha e avisa se o firmware estiver sem Wi-Fi.

---

## 5. Enviando o projeto para a placa

### Pelo MicroPico (o jeito do dia a dia)

1. Conecte a placa: a barra inferior do VS Code deve mostrar **Board Connected**.
2. `Ctrl+Shift+P` → **MicroPico: Upload Project to Board**.
3. Reinicie a placa para rodar o `main.py`: `Ctrl+Shift+P` → **MicroPico: Reset > Soft**, ou tire e coloque o cabo.

O arquivo `.vscode/settings.json` do projeto, na lista `micropico.pyIgnore`, diz ao MicroPico para **não** enviar a pasta `app_pc/` (ela roda no computador, não na placa), o `README.md` e o `config.py.example`.

Na placa devem ficar estes arquivos:

```
/
├─ main.py
├─ config.py         (só se você enviou ou configurou pelo app)
├─ lib/
│  ├─ aht10.py   display.py   matriz.py   medidas.py
│  ├─ perifericos.py   ponte_usb.py   rede.py
│  └─ servidor.py   ssd1306.py
└─ web/
   └─ index.html
```

### Pela linha de comando (alternativa)

Com o **mpremote**, a ferramenta oficial do MicroPython. Troque `COM8` pela porta da sua placa.

```bash
pip install mpremote
mpremote connect COM8 cp main.py : + cp -r lib web :
mpremote connect COM8 reset
```

---

## 6. Configurando o Wi-Fi

O Wi-Fi só é necessário para o **painel web**. O display, a matriz e o app do computador funcionam sem ele.

> O Pico W só conecta em redes de **2,4 GHz**. Redes só de 5 GHz não aparecem.

**Opção 1: pelo app do computador (mais fácil)**

1. Abra o app (seção 9).
2. No cartão **Wi-Fi da placa**, clique em **Procurar** e escolha a rede.
3. Digite a senha e clique em **Salvar na placa e conectar**.
4. A placa grava a rede no seu próprio `config.py` e conecta. O IP aparece no app e na tela **WI-FI** do OLED.

**Opção 2: pelo arquivo `config.py`**

1. Copie `config.py.example` para `config.py`.
2. Preencha `WIFI_SSID` e `WIFI_SENHA`.
3. Envie o arquivo para a placa (*Upload Project to Board* ou `mpremote cp config.py :`).

Nos dois casos a placa lembra a rede: ao ligar, ela conecta sozinha, e se a conexão cair, reconecta sozinha.

> O `config.py` contém a senha e por isso está no `.gitignore`: **nunca o envie para o GitHub**.

---

## 7. Usando a estação

### Ao ligar

1. O LED RGB pisca **vermelho, verde e azul**, o buzzer dá um bipe curto e a matriz acende um LED de cada vez. Isso é um **autoteste** das saídas.
2. O OLED mostra o resultado do **scan I2C** (os endereços encontrados) e "AHT10 OK". Se o sensor não for encontrado, aparece **ERRO: SEM AHT10**, e a placa tenta de novo a cada 3 s.
3. As leituras começam.

### Botões

| Botão      | Ação                                                                                        |
| ----------- | --------------------------------------------------------------------------------------------- |
| **A** | troca a tela do OLED (4 telas, em ciclo)                                                      |
| **B** | com o alarme tocando, **silencia**. Apertando de novo, o alarme **volta a tocar** |

### Telas do OLED

As bolinhas no canto superior direito mostram em qual tela você está.

| Tela                | O que mostra                                                                    |
| ------------------- | ------------------------------------------------------------------------------- |
| **AMBIENTE**  | temperatura em dígitos grandes, faixa de conforto, umidade e faixa de umidade  |
| **HISTORICO** | gráfico das últimas 60 leituras de temperatura, com escala automática        |
| **INFO**      | IP da placa, mínimas e máximas (▼ ▲) de temperatura e umidade, tempo ligado |
| **WI-FI**     | situação da conexão, nome da rede, IP e força do sinal                      |

Com o alarme ativo, aparece um **alto-falante** na barra de título: com ondas quando está tocando, com **X** quando está silenciado.

> As mínimas e máximas zeram quando a data do relógio da placa muda. Sem acertar o relógio pela internet, isso acontece a cada 24 h de funcionamento.

### Matriz de LEDs: termômetro de cores

A barra sobe de baixo para cima conforme a temperatura (15 °C ou menos acende 1 linha, 35 °C ou mais acende as 5), e a cor acompanha a faixa:

| ~17 °C | ~22 °C       | ~27 °C | ~30 °C | ~33 °C  |
| ------- | ------------- | ------- | ------- | -------- |
| azul    | verde-azulado | âmbar  | laranja | vermelho |

Entre esses pontos as cores se misturam, e as mudanças são suaves, levando cerca de 1 s. O brilho é limitado a **10 %**. Se o sensor falhar, só o LED do centro pisca em branco.

### LED RGB

Dá uma piscada **verde** a cada leitura boa e **vermelha** quando a leitura falha. Serve de "batimento" da placa.

---

## 8. Painel web

Com o Wi-Fi conectado, abra no navegador de qualquer aparelho **na mesma rede**:

```
http://<ip-da-placa>/
```

O IP aparece na tela **WI-FI** do OLED, na tela **INFO** e no app do computador.

O painel tem:

- cartões de temperatura e umidade, com mínima e máxima;
- um cartão de conforto, com sensação térmica, ponto de orvalho e situação do alarme;
- gráficos de temperatura e de umidade dos últimos 2 minutos, atualizados a cada 5 s. Passe o dedo ou o mouse sobre o gráfico para ver cada valor;
- uma tabela com os valores mais recentes ("Ver os valores em tabela").

Se a placa parar de responder, o indicador do topo fica **vermelho** ("sem resposta da placa").

O painel funciona **sem internet**: só a fonte *DM Sans* vem da internet. Sem ela, o navegador usa a fonte do sistema.

Para quem quiser usar os dados em outro programa, o endereço **`/api/leituras`** devolve tudo em JSON.

---

## 9. App do computador

Um programa em Python que conversa com a placa **pelo cabo USB**. Serve para a apresentação, porque o OLED é pequeno demais para a plateia ver.

- **Espelho do OLED** em tamanho grande, pixel por pixel, igual ao display;
- **botões A e B** na tela;
- **cartões** com as leituras;
- **configuração do Wi-Fi** e IP da placa;
- **terminal da placa**;
- **tela cheia** para projetor: botão no topo ou **F11**; **Esc** sai. Na tela cheia, o espelho fica ainda maior.

### Como rodar

```bash
cd app_pc
pip install -r requirements.txt     # instala o pyserial (só na primeira vez)
python estacao_pc.py                # procura a BitDogLab sozinho
python estacao_pc.py --porta COM8   # ou indique a porta
```

> **Só um programa por vez pode usar a porta USB.** Antes de abrir o app, desconecte o MicroPico no VS Code (clique em **Board Connected** na barra inferior). Para voltar a usar o MicroPico, feche o app.

Se o app mostrar **"Programa parado · clique Reiniciar"**, a placa está no prompt `>>>` (por exemplo, depois de um *Stop* no MicroPico). Clique em **Reiniciar placa**.

---

## 10. Como o código funciona

### Estrutura

```
estacao-ambiente/
├─ main.py               laço principal: cria as tarefas e liga tudo
├─ config.py.example     modelo do arquivo com a rede Wi-Fi
├─ lib/
│  ├─ aht10.py           driver do sensor (escrito do zero, sem biblioteca)
│  ├─ ssd1306.py         driver oficial do OLED (micropython-lib)
│  ├─ display.py         as 4 telas, dígitos grandes e ícones
│  ├─ matriz.py          termômetro de cores e mapeamento zigue-zague
│  ├─ medidas.py         média móvel, histórico, faixas, sensação térmica, alarme
│  ├─ perifericos.py     LED RGB, buzzer e botões
│  ├─ rede.py            Wi-Fi: conecta, reconecta, grava a configuração
│  ├─ servidor.py        servidor web (painel e /api/leituras)
│  └─ ponte_usb.py       conversa com o app do computador pelo USB
├─ web/index.html        a página do painel (HTML + CSS + JavaScript)
├─ app_pc/               app do computador (roda no PC, não na placa)
├─ .vscode/settings.json configurações do MicroPico e do editor
└─ .gitignore            deixa o config.py fora do Git
```

Todos os pinos ficam no topo do `main.py`. Os limites de conforto ficam no topo do `medidas.py`.

### Várias tarefas "ao mesmo tempo" com asyncio

O `main.py` não usa `time.sleep()` no laço. Cada parte é uma **tarefa** do `asyncio`, que cede a vez com `await` enquanto espera:

| Tarefa              | O que faz                                                   |
| ------------------- | ----------------------------------------------------------- |
| `tarefa_leitura`  | lê o sensor a cada 2 s, atualiza o `Estado`, pisca o LED |
| `tarefa_display`  | redesenha o OLED quando há novidade (ou a cada 1 s)        |
| `matriz.animar`   | desenha a matriz 25 vezes por segundo                       |
| `tarefa_alarme`   | bipa a cada 10 s enquanto o alarme estiver tocando          |
| `tarefa_botoes`   | lê os botões A e B a cada 20 ms                           |
| `rede.manter`     | conecta e reconecta o Wi-Fi                                 |
| `tarefa_servidor` | abre o painel web quando o Wi-Fi conecta                    |
| `ponte.escutar`   | recebe os comandos do app do computador                     |

Por exemplo: enquanto o AHT10 leva 80 ms medindo, a matriz continua animando e o painel continua respondendo.

### O driver do AHT10

O sensor é controlado por comandos I2C (veja `lib/aht10.py`):

1. **Inicializar:** enviar `0xE1 0x08 0x00` e esperar 20 ms.
2. **Medir:** enviar `0xAC 0x33 0x00`, esperar ~80 ms e ler 6 bytes. Se o bit 7 do primeiro byte (*ocupado*) estiver em 1, esperar e ler de novo.
3. **Converter** (2²⁰ = 1 048 576):

```
umidade (%)      = ((b1 << 12) | (b2 << 4) | (b3 >> 4)) / 2^20 × 100
temperatura (°C) = (((b3 & 0x0F) << 16) | (b4 << 8) | b5) / 2^20 × 200 − 50
```

Se der erro de comunicação, o driver devolve `None` em vez de travar o programa, e na leitura seguinte inicializa o sensor de novo. Assim dá para tirar e recolocar o cabo com a placa ligada.

### A conversa com o app (protocolo USB)

O USB do Pico é uma porta serial: o que o programa imprime com `print()` chega ao computador. Placa e app trocam **linhas de texto que começam com `@`**:

```
placa → PC   @TELA <imagem do OLED em base64>   @DADOS {...}   @WIFI {...}
PC → placa   @{"cmd": "ola"}   @{"cmd": "botao", "qual": "A"}   @{"cmd": "wifi", ...}
```

As linhas sem `@` são o terminal normal. A explicação completa está no topo de `lib/ponte_usb.py`.

### Cuidados com a memória

- O buffer da leitura do sensor e o histórico (`array('f')`) são criados uma vez e reaproveitados.
- O `gc.collect()` roda depois da inicialização e depois de cada página atendida pelo servidor.
- A página web sai da flash em pedaços de 512 bytes, em vez de ser carregada inteira na RAM.

---

## 11. Dicas para a apresentação

- **Teste a rede antes do dia.** Redes de escola costumam **bloquear a comunicação entre aparelhos** (o "isolamento de clientes") ou pedir login numa página antes de liberar a internet. Nesses casos a placa não conecta ou o painel não abre.
- **Plano B:** ative o **roteador do seu celular**, configure essa rede na placa pelo app e conecte nela os celulares de quem for ver o painel.
- Para mostrar no projetor, use o **app em tela cheia** (F11).
- Para mostrar o alarme, aqueça o sensor com a mão ou com o ar morno de um sopro. Acima de 32 °C a barra fica vermelha e o buzzer começa a bipar.
- Leve um cabo USB de dados de reserva.

---

## 12. Problemas comuns

### Sensor não encontrado (OLED mostra "ERRO: SEM AHT10")

- Confira os 4 fios: **VIN → 3V3**, **GND → GND**, **SDA → GP0**, **SCL → GP1**.
- **Inverta SDA e SCL**: é o erro mais comum.
- Veja os endereços que o scan encontrou (no OLED e no terminal):
  - nenhum endereço: problema de fio ou de alimentação;
  - um endereço diferente de `0x38` (como `0x29`, sensor de distância VL53L0X): **é outro sensor**, confira se ligou o AHT10.
- Se o sensor for um **AHT20/AHT21**, ele também aparece em `0x38`, mas o comando de inicialização é outro (`0xBE` em vez de `0xE1`). Ajuste `CMD_INICIALIZAR` em `lib/aht10.py`.

### OLED em branco

- Veja se o programa está rodando: o LED RGB deve piscar verde a cada 2 s. Se não pisca, a placa está parada no `>>>`. Reinicie (*MicroPico: Reset > Soft*, tirar e colocar o cabo, ou **Reiniciar placa** no app).
- No terminal, o scan do OLED deve mostrar `0x3C  OLED SSD1306`. Se não aparecer nada, a sua versão da BitDogLab pode usar outros pinos: confira e ajuste `PINO_OLED_SDA` / `PINO_OLED_SCL` no `main.py`.
- Se aparecer `0x3D` em vez de `0x3C`, mude `ENDERECO_OLED` em `lib/display.py`.
- A tela "congela" quando o programa é parado no MicroPico: isso é normal. Ela volta ao reiniciar.

### Wi-Fi não conecta

Veja a situação na tela **WI-FI** do OLED (ou no app):

| Mensagem               | O que fazer                                                                                                                                                                                                                                          |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **SEM CONFIG**   | nenhuma rede salva: configure pelo app ou pelo `config.py` (seção 6)                                                                                                                                                                             |
| **SENHA ERRADA** | redigite a senha (maiúsculas e minúsculas contam)                                                                                                                                                                                                  |
| **REDE AUSENTE** | a placa não encontrou a rede: confira o nome, a distância do roteador e se a rede é de **2,4 GHz**                                                                                                                                          |
| **FALHOU**       | a placa tenta de novo sozinha (após 2 s, 5 s, 10 s e depois a cada 30 s). Logo depois de ligar, uma primeira falha é normal. Redes com página de login (portal cativo) e redes corporativas (WPA-Enterprise, com usuário e senha) não funcionam |

- A senha do Wi-Fi precisa ter de 8 a 63 caracteres (ou ser vazia, para redes abertas).
- Confira se o firmware é o **do Pico W** (seção 4). O firmware do Pico comum não tem Wi-Fi.

### O painel web não abre

- O celular precisa estar **na mesma rede** que a placa. Desligue os dados móveis para testar.
- Digite **`http://`** (não `https://`) seguido do IP mostrado no OLED.
- O IP pode mudar quando a placa reconecta: confira de novo na tela **WI-FI**.
- Se a rede tiver isolamento de clientes (comum em escolas), use o roteador do celular (seção 11).

### O app do computador não conecta

- **"COM8 em uso · feche o MicroPico"**: a porta está aberta em outro programa. No VS Code, clique em **Board Connected** para desconectar.
- **"Programa parado · clique Reiniciar"**: clique em **Reiniciar placa**.
- **"Nenhuma placa · confira o cabo USB"**: troque o cabo (pode ser só de carga) ou a porta USB.
- **"Falta o pyserial"**: rode `pip install -r requirements.txt` na pasta `app_pc`.

### A luz da matriz anda na ordem errada no autoteste

O mapeamento do zigue-zague (`indice()` em `lib/matriz.py`) segue a fiação mais comum da BitDogLab, com o LED 0 no canto de baixo à direita. Se na sua placa a luz não andar da esquerda para a direita e de cima para baixo, ajuste essa função: o desenho no comentário dela mostra a numeração.

### O buzzer não para

Aperte **B** para silenciar. Se o programa foi interrompido no meio de um bipe, reinicie a placa. Ao parar normalmente (Ctrl+C ou Stop no MicroPico), o programa já desliga o buzzer, o LED e a matriz.

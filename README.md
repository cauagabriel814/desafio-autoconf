# Agente de pré-venda Autoconf

Primeiro atendimento de uma revenda de seminovos: entende o que o cliente procura, oferece só o que existe no estoque, preenche a ficha do lead e passa para um vendedor em reclamação, negociação, pedido de crédito ou urgência. Funciona por WhatsApp (Evolution API), por uma tela de chat no navegador e pelo terminal.

Arquitetura completa em [diagrama-arquitetura.html](diagrama-arquitetura.html). Decisões e o que ficou de fora em [NOTA.md](NOTA.md).

## Como rodar

Precisa só do **Docker** (com Docker Compose).

```bash
cp .env.example .env        # e preencha OPENAI_API_KEY
docker compose up -d --build
```

Abra **http://localhost:8000**.

| Tela | Endereço |
|---|---|
| Chat do cliente (texto, áudio, imagem) | http://localhost:8000/chat |
| Leads e ficha do lead | http://localhost:8000/painel |
| Exportar para CRM | http://localhost:8000/painel/exportar |
| Estoque (CRUD) | http://localhost:8000/painel/estoque |
| Conectar WhatsApp (QR code) | http://localhost:8000/painel/whatsapp |
| Resultado dos testes do agente | http://localhost:8000/painel/testes |
| Configuração do agente | http://localhost:8000/painel/config |
| API (Swagger) | http://localhost:8000/docs |
| Langfuse (traces) | http://localhost:3000 · `admin@autoconf.local` / `autoconf123` |
| MinIO (console do S3 do Langfuse) | http://localhost:9001 · `minio` / `miniosecret` |
| Evolution API | http://localhost:8080 (header `apikey` = `EVOLUTION_API_KEY`) |

Todos nas portas padrão de cada ferramenta. Bancos, Redis e ClickHouse ficam só na rede interna do compose. O passo a passo de cada tela está em [Como usar](#como-usar).

O compose sobe três blocos:
- **app**: agente, API e telas;
- **Evolution API** (+ Postgres e Redis): WhatsApp. Ela chama o webhook do app pela rede interna do compose; ngrok só é preciso com uma Evolution externa (veja abaixo);
- **Langfuse v3** (web, worker, Postgres, ClickHouse, Redis e MinIO): monitoramento self-hosted. A organização, o projeto e as chaves são criados na primeira subida, sem passo manual.

> **Rede e exposição.** O ngrok **não sobe por padrão** (só com `--profile ngrok`) e, quando sobe, expõe só o app (porta 8000), nunca a Evolution. Fora isso nada fica na internet. As portas 8000, 8080, 3000, 9000 e 9001 ficam abertas na rede local da máquina; para deixar só nela mesma, troque no `docker-compose.yml` `"8000:8000"` por `"127.0.0.1:8000:8000"` (e o mesmo nas outras).

São 11 containers (12 com o ngrok opcional). O `app` tem healthcheck: `docker compose ps` mostra `healthy` quando ele já responde (a subida leva uns 15 s). Durante esses segundos as páginas não abrem; é só esperar e dar F5. O ClickHouse do Langfuse pede uns 2 GB de RAM livres para o Docker. Portas usadas: 8000, 8080, 3000, 9000, 9001 (e 4040 com ngrok).

### WhatsApp

Há dois jeitos de ligar o WhatsApp. Escolha conforme **onde roda a Evolution API**.

#### A) Evolution deste compose (padrão, sem ngrok)

A Evolution sobe junto (`http://localhost:8080`) e chama o webhook do app pela rede interna do Docker (`http://app:8000/webhook/evolution`). Nada precisa ficar exposto na internet: a conexão do WhatsApp é de saída.

1. `docker compose up -d --build`
2. Abra **http://localhost:8000/painel/whatsapp** e leia o QR code com o celular (WhatsApp → Aparelhos conectados → Conectar um aparelho). O estado `connecting` com o QR na tela é normal até você ler.
3. Recarregue a página: o estado deve virar `open`.
4. **De outro número**, mande uma mensagem começando com `#thread`. Sem a palavra-chave, o bot não responde.

#### B) Evolution externa (servidor, nuvem ou conta já existente) → precisa de ngrok

Uma Evolution que roda fora da sua máquina não alcança o `localhost`. É preciso expor o app com o **ngrok** e cadastrar esse endereço como webhook.

1. Crie uma conta grátis em https://ngrok.com e copie o **authtoken** (Dashboard → Your Authtoken).
2. No `.env`:
   ```env
   EVOLUTION_API_URL=https://sua-evolution.exemplo.com
   EVOLUTION_API_KEY=a-chave-da-sua-evolution
   EVOLUTION_INSTANCE=nome-da-instancia
   NGROK_AUTHTOKEN=seu-authtoken
   ```
3. Suba com o ngrok:
   ```bash
   docker compose --profile ngrok up -d --build
   ```
4. Abra **http://localhost:4040** e copie o endereço público (`https://xxxx.ngrok-free.app`). Ele também aparece em `docker compose logs ngrok`.
5. Cadastre o webhook na **sua** Evolution apontando para o app:
   ```bash
   curl -X POST "https://sua-evolution.exemplo.com/webhook/set/nome-da-instancia" \
     -H "apikey: a-chave-da-sua-evolution" -H "Content-Type: application/json" \
     -d '{"webhook": {"enabled": true, "url": "https://xxxx.ngrok-free.app/webhook/evolution",
          "events": ["MESSAGES_UPSERT"], "base64": true}}'
   ```
6. Conecte o número pela tela da sua Evolution (ou em `/painel/whatsapp`, que usa o `EVOLUTION_API_URL`) e mande `#thread` de outro número.

> Sem ngrok instalado no Docker? Funciona igual com o ngrok da máquina: `ngrok http 8000`, e use o endereço que ele mostrar no passo 5.
> No plano grátis o endereço muda a cada vez que o ngrok reinicia: refaça o passo 5 quando isso acontecer.

#### Problemas comuns

| Sintoma | O que fazer |
|---|---|
| QR não aparece / "Não consegui falar com a Evolution" | `docker compose ps` (o container `evolution` está `running`?) e `docker compose logs evolution --tail 50`. Confira se `EVOLUTION_API_KEY` é a mesma no `.env` e na Evolution. |
| Leu o QR mas o bot não responde | A mensagem tem `#thread`? Mandou de **outro** número (o bot ignora as mensagens do próprio número)? Veja `docker compose logs app --tail 50`. |
| Evolution externa não chama o webhook | O ngrok está de pé (`http://localhost:4040`)? O webhook cadastrado termina em `/webhook/evolution`? Refaça o passo 5 se o endereço mudou. |
| Áudio chega mas não é entendido | Confira a `OPENAI_API_KEY`; o áudio é transcrito pela OpenAI. |

Depois do primeiro `#thread`, áudios e imagens passam por 30 minutos sem a palavra-chave (áudio não tem como trazer o texto).

### Comandos de teste (só com `TEST=on`, só no WhatsApp)

Com `TEST=on` no `.env`, dois comandos ajudam a testar com o mesmo celular. Com `TEST=off` (padrão) eles são texto normal. Na tela de chat nunca valem: lá existe o botão "Nova conversa".

| Comando | O que faz |
|---|---|
| `#reset` | Arquiva a conversa (fica no painel como "arquivado") e começa uma nova do zero no mesmo número. |
| `#parar` | O agente confirma e para de responder. As mensagens seguintes só ficam gravadas. |
| `#thread` | Depois de um `#parar`, religa o agente na mesma conversa (com a ficha e o histórico). |

O simulador liga o modo de teste sozinho, porque tem casos para esses comandos.

### Terminal

```bash
docker compose run --rm app python -m app.terminal
```

## Como usar

Um roteiro para conhecer o agente em uns 10 minutos, todo pelo navegador.

### 1. Conversar como cliente (`/chat`)

1. Abra **http://localhost:8000/chat**. A tela imita o WhatsApp e não precisa de `#thread`.
2. Mande uma mensagem como um cliente mandaria. Exemplo de conversa completa:
   - "Oi" → a Lia se apresenta: *"Boa tarde! Aqui é a Lia, da Autoconf Veículos 😊 Como posso te ajudar?"*
   - "Quero um SUV" → ela já mostra **os SUVs da loja, do mais barato ao mais caro**, e pergunta **quanto você pensa em investir** para afinar a lista.
   - "Uns 120 mil" → ela mostra **só os que cabem no valor**, comenta um ou dois e pergunta **se você gostou de algum**.
   - "Gostei do Kicks" → ela comenta o carro e pergunta se é **à vista ou financiado**.
   - "À vista" → ela convida para a loja: **que dia e período você consegue vir ver o carro?**
   - "Sábado de manhã" → com a visita combinada, o lead fica **qualificado**: ela anota o dia e avisa que o vendedor vai chamar para confirmar o horário.
   - Para **vender** o carro em vez de comprar: "Vocês compram carro? Quero vender o meu" → ela pergunta o carro (modelo e ano) e o dia para trazer à avaliação, sem dizer valor.
3. Também dá para:
   - **gravar áudio**: com o campo vazio, o botão vira microfone. A resposta vem em áudio;
   - **mandar imagem**: pelo clipe. Ela descreve a imagem e responde em texto.
4. Para começar outro cliente do zero, clique em **Nova conversa**.

Outras situações para testar, as mesmas do desafio:

| Mande | O que a Lia faz |
|---|---|
| Quero um SUV até 120 mil, tenho um Gol 2018 pra troca e quero financiar | Mostra só os SUVs até R$ 120 mil, registra a troca, não promete crédito e pergunta se gostou de algum; depois, o dia da visita |
| Tem Hilux 2024? | Diz que não tem, sugere 2 picapes do estoque e pergunta a faixa de preço para mostrar mais opções |
| Preciso de um carro pra essa semana, o meu quebrou | Percebe a pressa e passa para o vendedor com urgência alta |
| É a terceira vez que eu cobro o documento do carro. Vou no Procon. | Pede desculpas, sem oferecer carro, e passa para uma pessoa |
| Fecho no Corolla se fizer 115 mil | Não negocia e passa para o vendedor |
| Qual a cotação do dólar? | Não trava: diz que só ajuda com carros e puxa a conversa de volta |
| Quanto vocês pagam no meu Onix 2019? | Não estima valor: quem avalia é o vendedor, e passa a conversa (negociação) |
| O Creta tem garantia? | Não inventa: o estoque não tem essa informação, então confirma com o vendedor e passa a conversa |

### 2. Ver o lead que a conversa gerou (`/painel`)

1. Abra **Leads**. Cada conversa vira um lead, com status:
   - **bot**: a Lia ainda está atendendo;
   - **humano**: passou para o vendedor;
   - **parado**: o cliente mandou `#parar`;
   - **arquivado**: a conversa foi arquivada pelo `#reset`.
2. Filtre por canal, status ou urgência. Os leads do simulador ficam escondidos por padrão.
3. Clique num lead para abrir a **ficha**:
   - as perguntas da qualificação (compra: modelo ou tipo, valor, como quer comprar e dia da visita; venda: o carro e o dia da avaliação), cada uma com a situação: respondido, ainda não perguntado, perguntado sem resposta ou cliente não soube;
   - o que mais o cliente contou: troca, entrada, urgência;
   - o **resumo para o vendedor** (botão **Gerar resumo**), com o motivo da transferência e o que ainda falta perguntar;
   - a conversa inteira, inclusive as respostas que o guardrail barrou.
4. Se o vendedor quiser devolver o cliente para a Lia, use **Devolver ao bot**. O botão aparece quando o lead está com status humano.

### 3. Exportar para o CRM (`/painel/exportar`)

Escolha o CRM e baixe o CSV, que já vem com as colunas dele. Só entram leads válidos: WhatsApp ou site, não arquivados e com alguma informação de compra. A tela mostra quantos ficaram de fora.

### 4. Cuidar do estoque (`/painel/estoque`)

- **Adicionar um carro**: escolha a marca, o modelo e o ano. O tipo (SUV, sedã, hatch, picape) é preenchido pelo modelo. No preço, digitar `55` vira R$ 55.000.
- **Editar ou excluir**: pelos botões **Editar** e **Excluir** na linha do carro.
- **Importar vários**: arraste um CSV para a área de importação. O botão **Baixar modelo** traz um CSV de exemplo.

A Lia só oferece o que está nessa lista: um carro adicionado aqui já aparece na próxima conversa.

### 5. Rodar os testes do agente (`/painel/testes`)

1. Clique em um dos botões:
   - **Rodar casos do PDF**: 7 conversas, cerca de 1 min;
   - **Rodar coleta básica**: 21 conversas, cerca de 4 min;
   - **Rodar respostas da Autoconf**: 6 conversas (vender o carro, valor do carro do cliente, pergunta sem resposta no estoque, modelo sem estoque, visita), cerca de 1 min;
   - **Rodar todos (com humanidade)**: os 78 casos e a nota de humanidade, cerca de 18 min.
2. Enquanto roda, a tela mostra a barra de progresso, o caso atual e os que estão na fila. Cada conversa aparece completa logo abaixo assim que termina, com as mensagens, as verificações (✓/✗), a ficha e o resumo.
3. No fim, use os filtros **Casos do PDF** e **Só falhas**. A rodada completa e a última parcial ficam guardadas, e dá para alternar entre elas.

Os testes usam o modelo da OpenAI de verdade, então gastam créditos.

### 6. Ajustar o agente sem reiniciar (`/painel/config`)

- **Quando responder em áudio**: espelhar o cliente, sempre texto, sempre áudio ou os dois.
- **Voz**, nome da atendente e nome da loja.
- **Palavra-chave do WhatsApp** (`#thread`) e **modo de teste** (`#reset`/`#parar`).

Salvou, vale na próxima mensagem. **Voltar ao .env** desfaz os ajustes. Os modelos e as versões ficam só para consulta.

### 7. Ligar o WhatsApp (`/painel/whatsapp`)

1. Leia o QR code com o celular que vai ser o número da loja (WhatsApp → Aparelhos conectados).
2. De **outro** celular, mande uma mensagem começando com `#thread`, por exemplo: `#thread oi, quero um SUV`.
3. A conversa aparece no painel como lead do canal WhatsApp.

Com `TEST=on`, `#reset` recomeça a conversa do zero e `#parar` faz a Lia parar de responder. Para religar depois do `#parar`, mande `#thread`. Detalhes e Evolution externa em [WhatsApp](#whatsapp).

### 8. Acompanhar o que o agente fez (Langfuse)

Abra **http://localhost:3000** e entre com `admin@autoconf.local` / `autoconf123`. Cada mensagem do cliente vira um trace, com:
- a extração da ficha;
- as chamadas ao modelo e às ferramentas (busca no estoque, transferência);
- os guardrails;
- o tempo de cada passo.

## Testes

Dois níveis:

| | O que testa | Usa o modelo? |
|---|---|---|
| **Simulador** (78 casos) | O agente de ponta a ponta: os 6 casos do PDF + qualificação até a visita (itens parciais, quando NÃO qualificar), **respostas da Autoconf** (`a1`–`a6`: vender o carro, quanto a loja paga no carro do cliente, garantia e histórico que o estoque não tem, modelo sem estoque → pede a faixa de preço, cliente que já fala em ir à loja), **coleta básica dita de 21 jeitos** (`c01`–`c21`: "80k", "uns 80 conto", faixa de preço, "parcelado", "metade à vista", marca, fora de ordem, tudo numa frase com erro de digitação, correção do valor, "não sei", pergunta no meio, áudio, só o tipo → lista + pergunta o valor, "quero ver os carros" → lista na hora, "gostou de algum?" depois da lista, "não gostei de nenhum", "sedan" escrito em inglês), listagem do estoque em ordem de preço, marcas no áudio, saudação, limites (desconto, parcela, reserva), transferências e comandos | Sim |
| **Bateria de guardrails** (51 casos) | Entrada (32 mensagens, incluindo dia da visita, ano solto e "quero vender o meu": permitidas, fora do assunto, injeção, ofensa) e saída (19 respostas: as que devem passar e as que devem ser barradas) | Sim |
| **Tipos de arquivo** (16 casos) | Áudios e imagens em cada formato aceito + os não suportados (PDF, HEIC, vídeo, vazio, grande demais) pelo `/chat` | Sim |
| **pytest** (288 testes) | Tools, checklist, sugestões, filtros, guardrails, comandos, exportação, e as **telas e a API pelo HTTP** (páginas, chat, CRUD de estoque, exportação, webhook) | Não |

```bash
# Simulador: gera relatorios/resultado.md e a tela http://localhost:8000/painel/testes
docker compose exec app python -m simulador
docker compose exec app python -m simulador --tom      # + juiz de humanidade (nota 1 a 5 por resposta)
docker compose exec app python -m simulador 1 4 x2     # só alguns casos (prefixo do id)

# Guardrails com o modelo de verdade: gera relatorios/guardrails.md
docker compose exec app python -m simulador.guardrails

# Todos os tipos de arquivo pelo /chat (áudios gerados pela voz da OpenAI, imagens com Pillow)
docker compose exec app python -m simulador.arquivos

# Testes unitários e das telas (não precisam de chave, não gastam modelo)
docker compose run --rm --no-deps app python -m pytest -q
```

Última rodada completa (salva em `relatorios/`), já com as respostas da Autoconf: **simulador 75/78**, humanidade média **4,1 de 5** em 134 respostas (27 com nota 5, 97 com 4, 9 com 3, 1 com 2). **Guardrails 51/51**, **arquivos 16/16**, **pytest 288/288**. Das 3 falhas, o `c21` foi corrigido depois da rodada (passou nas 3 repetições seguintes); as outras duas estão em "Onde o agente ainda erra" abaixo.

Os casos ficam em [simulador/casos.yaml](simulador/casos.yaml). A tela **Testes** do painel (`/painel/testes`) também **roda os testes**, sem terminal: há botões para os **casos do PDF** (7 conversas, cerca de 1 min), para a **coleta básica** (21 conversas, cerca de 4 min), para as **respostas da Autoconf** (6 conversas, cerca de 1 min) e para **todos** com nota de humanidade (cerca de 18 min). A tela mostra caso a caso enquanto roda e, no fim, abre o resultado: placar, cada conversa, as verificações, a ficha final e o resumo para o vendedor. O filtro **Casos do PDF** mostra só as 6 situações da tabela do desafio.

## Exemplos de verificação (resultados salvos)

Os relatórios da última rodada completa ficam no repositório, em [relatorios/](relatorios/):

| Arquivo | O que tem |
|---|---|
| [resultado.md](relatorios/resultado.md) | As conversas do simulador: cada mensagem, a resposta, as tools chamadas, os bloqueios dos guardrails, as verificações (✅/❌), a ficha final, o resumo para o vendedor e a nota de humanidade, com as piores respostas no fim |
| [guardrails.md](relatorios/guardrails.md) | Cada mensagem de entrada e cada resposta de saída testada, com o esperado, o real e o motivo |
| [arquivos.md](relatorios/arquivos.md) | Cada tipo de arquivo enviado pelo chat e o que aconteceu |
| `resultado.json` | O mesmo do simulador, usado pela tela **Testes** |

Rodadas parciais (`python -m simulador c01 x4`) vão para `resultado_parcial.md`, que não entra no repositório.

### Onde o agente ainda erra

- **`c09` — "Quero um carro pequeno e econômico pra cidade"**: o cliente disse o uso, não o tipo. O certo seria perguntar o tipo (hatch? sedã?), mas o agente perguntou "algum modelo em mente ou uma faixa de preço?". A ficha fica certa (tipo 🟡), só a pergunta sai genérica.
- **`x33` — "#thread" sozinho**: deveria receber a saudação fixa, mas na rodada completa saiu uma saudação do modelo ("E aí, como tá? ... O que posso ajudar hoje?"). Repetido sozinho, passou 2 de 2: é intermitente e a causa não foi encontrada.
- **`c21` (corrigido depois da rodada):** na mensagem da visita, a extração tirou a marca "Fiat" do texto da própria Lia, e a resposta listou carros da Fiat (um hatch e uma picape) depois de avisar do vendedor. Agora modelo citado só pela Lia não conta como interesse, e a mensagem que transfere não pode buscar estoque.
- **A pergunta do fim nem sempre sai:** às vezes, depois da lista por tipo, o agente termina com "me avisa se algum te interessar" em vez de perguntar o valor; ele pergunta na mensagem seguinte.
- **Humanidade (média 4,1):** a única nota 2 foi a saudação fixa depois do `#reset` (`x30`), que o juiz acha genérica. Várias notas 3 citam "assistente virtual" (quando o cliente pergunta se é robô) e aberturas com cara de script. A Lia não esconde que é virtual quando perguntada diretamente; isso foi decidido de propósito (ver [Humanidade](#humanidade)). As piores respostas estão listadas no fim de [resultado.md](relatorios/resultado.md).

## Variáveis (.env)

| Variável | Para quê |
|---|---|
| `OPENAI_API_KEY` | LLM, transcrição, visão e voz. **Obrigatória.** |
| `EVOLUTION_API_KEY` | Chave da Evolution. O compose usa a mesma no container dela e no app. |
| `EVOLUTION_INSTANCE` | Nome da instância do WhatsApp (padrão `autoconf`). |
| `TRIGGER_KEYWORD` | Palavra-chave do WhatsApp (padrão `#thread`). |
| `TEST` | `on` liga os comandos `#reset`/`#parar` no WhatsApp. Padrão `off`. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Chaves do projeto no Langfuse local. Vazias = padrão `pk-lf-autoconf-local` / `sk-lf-autoconf-local`. |
| `EVOLUTION_API_URL` | Só para Evolution externa (modo B). Vazio = a Evolution deste compose. |
| `NGROK_AUTHTOKEN` | Só para o modo B, com `--profile ngrok`. |
| `WEBHOOK_URL` | Webhook que a Evolution **deste** compose chama. Vazio = rede interna (`http://app:8000/webhook/evolution`). |

Modelos (opcionais, com padrão): `OPENAI_MODEL=gpt-4o-mini`, `OPENAI_STT_MODEL=gpt-4o-mini-transcribe`, `OPENAI_TTS_MODEL=gpt-4o-mini-tts`, `OPENAI_TTS_VOICE=coral`.

## Humanidade

O agente escreve como uma vendedora de verdade no WhatsApp, e isso é medido:

- **Persona.** A Autoconf prefere que ela não se identifique como robô e fique o mais próxima possível de um humano. A Lia se apresenta como "a Lia, da Autoconf Veículos" e nunca se descreve como robô, IA ou sistema por conta própria. Só não mente: se o cliente perguntar **diretamente** se fala com um robô, ela diz, numa frase leve, que é a assistente virtual da loja e que um vendedor assume quando ele quiser. Negar ser IA para quem pergunta engana o cliente e expõe a loja (CDC e transparência no uso de IA), por isso essa parte ficou fixa.

- **Reage antes de responder** ("Poxa, carro quebrado é um sufoco"), acompanha o jeito do cliente (informal com quem é informal), usa o nome de vez em quando e comenta 1 ou 2 carros como um vendedor faria ("o T-Cross é o mais novinho, só 19 mil km").
- **Balões e "digitando…"**: a resposta sai em 2 ou 3 balões (lista separada da pergunta), e no WhatsApp a Evolution mostra "digitando…" por um tempo proporcional ao tamanho de cada balão. A tela de chat faz o mesmo.
- **Não repete o jeito de começar**: o código passa ao agente as aberturas que ele já usou na conversa.
- **Frases de robô são barradas em código** ("Aqui estão algumas opções", "Como posso ajudar", "Fico à disposição"…): se aparecerem, o agente reescreve uma vez a mesma resposta. Markdown (`**`, `#`) é removido, porque no WhatsApp aparece como símbolo.
- **Passar para o vendedor sem cara de procedimento**: cada motivo tem um jeito (negociação: "que bom que você quer fechar!"; reclamação: desculpas de verdade, sem emoji).
- **Voz**: o áudio é gravado como um áudio de WhatsApp, com pausas e sorriso na voz, falando só o essencial.
- **Juiz de humanidade**: `python -m simulador --tom` dá nota de 1 a 5 a cada resposta e lista as piores. A média aparece na tela **Testes**.

## Como o agente decide

- **Qualificação até a visita, em código.** Pela resposta da Autoconf, qualificar é identificar a intenção do cliente até **agendar uma ida à loja**. Para **comprar**: **modelo/tipo de carro**, **valor**, **como quer comprar** (à vista ou financiado) e o **dia e período da visita**. Para **vender o carro dele**: o **carro** (modelo e ano) e o **dia para trazer à avaliação**; a Lia nunca diz quanto a loja paga. Não há agenda integrada: a Lia anota o dia e o vendedor confirma o horário. A cada mensagem, uma extração estruturada grava na ficha o que o cliente disse, e o código monta o checklist com o que já foi respondido e a próxima pergunta. O agente nunca volta numa pergunta respondida, e "não sei" também conta. Entrada, troca, prazo, idade e renda não são perguntados: se o cliente contar, vão para a ficha e para o resumo. Única exceção: se ele **citar** um carro de troca sem o ano (ou sem o modelo), o agente pergunta só essa parte.
- **Saudação fixa.** Um "oi" sozinho no começo da conversa recebe sempre a mesma abertura, sem passar pelo modelo: "Boa tarde! Aqui é a Lia, da Autoconf Veículos 😊 Como posso te ajudar?". O período repete o que o cliente disse ("bom dia") ou segue o horário de Brasília; se ele perguntou "tudo bem?", entra um "Tudo ótimo por aqui!".
- **Lista com o tipo e com o valor.** Como pediu a Autoconf (o estoque de seminovos é pequeno), a lista sai assim que o cliente diz o tipo ("quero um SUV"), com a pergunta do valor para afinar; com o valor, sai de novo só com os que cabem. Se o **modelo não existe** no estoque, a Lia diz isso, sugere 1 ou 2 parecidos e pergunta a faixa de preço; com a faixa, mostra tudo o que cabe, mesmo de outro tipo.
- **"Gostou de algum?"** Depois de mostrar os carros que cabem no valor, o agente pergunta se o cliente gostou de algum antes de seguir para a próxima pergunta. Se ele escolhe um, o agente comenta aquele carro (sem repetir a lista) e segue; se não gostou de nenhum, pergunta o que ele procura de diferente; se respondeu outra coisa (o ano da troca, a forma de pagamento), o checklist segue normal.
- **Carros escolhidos pelo código.** O código busca **todos** os carros que combinam (até 10), sempre **do mais barato ao mais caro**, e o agente só apresenta. Os destaques que ele pode comentar (mais novo, menos rodado, mais em conta) também são calculados no código, para não dizer que um carro é "o mais novinho" quando não é; e os comentários já feitos são passados de volta para ele não repetir.
- **Marcas e modelos no áudio.** A transcrição recebe como dica as marcas e os modelos do estoque e do catálogo, e o código corrige erros comuns ("build ID"/"BY de"/"bi uai di" → BYD, "agá bê vinte" → HB20, "vê dáblio" → VW). Marca sozinha ("tem algum Toyota?") conta como interesse: o código mostra os carros da marca ou avisa que ela não está no estoque. Áudio curto e confuso não é tratado como "fora do assunto": o agente pede para repetir ou para contar mais.
- **Itens parciais.** Se o cliente responde só uma parte (o carro da troca sem o ano, o uso sem o tipo de carro), o item fica 🟡 e a próxima pergunta é a parte que falta. Cada item é perguntado no máximo 2 vezes, para não entrar em laço.
- **Promessa só com transferência.** Se o agente disser que vai passar para o vendedor mas a transferência não for permitida (ex.: falta saber o tipo de carro), a resposta é reescrita sem a promessa, perguntando o que falta.
- **Transferência garantida.** A extração e uma regra de palavras-chave detectam reclamação, negociação (desconto, contraproposta e **quanto a loja paga no carro do cliente**), crédito/parcela, urgência, pedido de pessoa e **pergunta que a Lia não sabe responder**: o estoque só tem marca, modelo, ano, km, preço e tipo, então garantia, laudo, histórico, multas, IPVA, endereço ou horário vão para o vendedor (motivo `sem_resposta`) em vez de um chute. Se o agente não chamar `transferir_humano`, ele é cobrado uma vez e, se ainda assim não chamar, o código transfere.
- **Lead qualificado.** Com a visita combinada, o lead vai para o vendedor como **qualificado**, com o resumo e o dia anotado; o cliente é avisado de que o vendedor vai chamar para confirmar o horário. Se o cliente já disser o dia antes (ex.: "posso passar aí sábado?"), o dia fica anotado e a Lia só pergunta o que falta.
- **Guardrails só bloqueiam.** Entrada: fora do assunto, injeção, ofensa. Saída, em código: preço de cada carro conferido no banco (o valor tem que ser o preço **daquele** carro) e modelo oferecido tem que existir. LLM juiz para promessa, contraproposta e vazamento, que só vale se citar o trecho exato com evidência da regra.

## Telas

| Tela | O que faz |
|---|---|
| **Chat do cliente** | Simula o WhatsApp. Um botão só: microfone com o campo vazio, enviar quando há texto. Durante a gravação mostra "Gravando", o tempo e o nível do microfone; dá para cancelar. Imagem pelo clipe. |
| **Leads** | 10 por página, filtros por canal (os do simulador ficam escondidos por padrão), status e urgência. |
| **Ficha do lead** | Resumo para o vendedor, ficha, conversa (com as respostas que o guardrail barrou), devolver ao bot, exportar este lead. |
| **Exportar** | Escolha o CRM e veja só as colunas dele. Exporta só leads válidos: WhatsApp ou site, não arquivados, com alguma informação de compra. Nome e telefone sempre preenchidos no formato que o CRM aceita. |
| **Estoque** | CRUD com opções de marca, modelo (da marca) e ano; tipo preenchido pelo modelo; km e preço formatados (digitar 55 no preço = R$ 55.000). Importação de CSV por arrastar e soltar, com modelo para baixar. |
| **WhatsApp** | QR code e estado da conexão. |
| **Testes** | Última rodada do simulador: placar, nota de humanidade, conversas, verificações. |
| **Config** | Ajustes do agente que valem na hora, sem reiniciar (ver abaixo). Modelos, serviços, chaves e versões das bibliotecas ficam só para consulta. |

## Configuração do agente (tela e API)

Na tela **Config** (`/painel/config`) ou pela API (`GET /config`, `PUT /config`, `POST /config/restaurar`). O que for salvo fica no banco e vale por cima do `.env`, na hora.

| Pode mudar | Opções |
|---|---|
| **Quando responder em áudio** (`modo_resposta`) | `espelhar` (padrão: áudio → áudio, texto/imagem → texto) · `sempre_texto` · `sempre_audio` · `texto_e_audio` |
| Voz do áudio | alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse |
| Nome da atendente, nome da loja | texto |
| Palavra-chave (`#thread`), modo de teste, comandos `#reset`/`#parar` | texto / on-off |
| Espera para agrupar mensagens, sessão do `#thread`, mensagens de histórico | números (com limites) |

Só consulta (mudam no `.env`): modelo de conversa, transcrição e voz, endereços da Evolution e do Langfuse, banco, se as chaves estão configuradas, versões do Python e das bibliotecas.

```bash
curl -X PUT http://localhost:8000/config -H "Content-Type: application/json" -d '{"modo_resposta": "sempre_texto"}'
```

## Tipos de arquivo

| Tipo | O que acontece |
|---|---|
| Áudio ogg/opus (WhatsApp), webm (navegador), mp3, m4a/mp4, wav, flac | Transcrito (até 25 MB) |
| Imagem jpg, png, webp, gif | Descrita pelo modelo de visão (até 20 MB) |
| Qualquer outra coisa (aac, HEIC, PDF, documento, vídeo, figurinha, localização, arquivo vazio ou grande demais) | Resposta amigável: "esse arquivo eu não consigo abrir por aqui… pode mandar como foto, áudio ou me contar por texto?" |

Teste com arquivos de verdade: `docker compose exec app python -m simulador.arquivos` (gera `relatorios/arquivos.md`).

## Estrutura

```
app/
  canais/      entrada e saída por canal (evolution, web) + mensagem padrão
  pipeline/    filtros, mídia, guardrails, resposta e o orquestrador das etapas
  agente/      grafo LangGraph, tools, prompts, ficha (extração + checklist)
  db/          modelos SQLAlchemy, seed do estoque, consultas
  telas/       páginas Jinja2
  api.py       API das telas (leads, export, carros)
simulador/     casos de teste e relatório
tests/         pytest
```

Stack: Python 3.12, FastAPI, LangGraph + LangChain, OpenAI, SQLite + SQLAlchemy, Evolution API v2, Langfuse.

O `.env` (com as chaves), a pasta `data/` (banco e áudios) e o PDF do desafio não entram no repositório (ver `.gitignore`). Documentos de apoio: [NOTA.md](NOTA.md), [MELHORIAS.md](MELHORIAS.md) (oportunidades de melhoria) e [PLANEJAMENTO.md](PLANEJAMENTO.md) (o plano original).

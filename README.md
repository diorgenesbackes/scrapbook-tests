# scrapbook-tests

## Fundação local

Com os repositórios irmãos disponíveis, o runner cria um projeto Compose exclusivo, seleciona portas de loopback e usa credenciais sintéticas. Ele aplica explicitamente a base do Liquibase e exercita indisponibilidade, recuperação, persistência e configuração inválida. Ao terminar, remove os recursos criados para aquela execução.

```sh
python3 docker_stack.py
```

Para acrescentar navegador, teclado e verificação automatizada de acessibilidade, preparar Node 26.13.0, npm 11.20.0 e os navegadores do Playwright.

```sh
npm ci
npm exec -- playwright install chromium firefox webkit
python3 docker_stack.py --browser
```

`npm run test:foundation` exercita somente navegador em uma stack já preparada. Use `PLAYWRIGHT_BASE_URL` para escolher outra porta de `127.0.0.1`. As emulações de Android e iPhone verificam layout e interação. Dispositivos físicos e leitores de tela exigem evidência manual própria. [Ambiente local](../scrapbook-infrastructure/README.md)

## Criação, listagem e edição de eventos

`python3 -B event_stack.py` prepara PostgreSQL e três identidades descartáveis. Exercita criação, repetição, detalhe, paginação, alterações concorrentes da política de abertura, edição concorrente dos detalhes, isolamento, travas, prazos e reversão em caso de falha da auditoria. Acrescente `--browser` para as jornadas Playwright e `--load` para medir criação, listagem, edição de detalhes e alterações de política com k6 em loopback.

## Estado de abertura das lembranças

`python3 -B event_access_stack.py` cria uma cópia descartável do serviço com o relógio de teste montado como arquivo somente leitura. O código e a imagem de publicação usam o relógio do sistema. O teste controla a meia-noite de Brasília sem alterar o relógio do host ou executar um serviço de avisos. Acrescente `--browser` para a jornada Playwright e `--load` para medir consultas abertas e fechadas com k6.

`python3 -B event_public_stack.py --browser --load` valida a página dos convidados e o compartilhamento com eventos sintéticos e relógio controlado. O comando sem flags roda HTTP e PostgreSQL para conferir os dois responsáveis, conta sem vínculo, autenticação, estabilidade do código após edição, prazos, campos públicos, headers e telemetria. `--browser` acrescenta Playwright para cópia, seleção manual, decodificação do QR na tela e nos arquivos, dimensão na prévia de impressão e abertura sem sessão. `--load` roda k6 com 200 convidados em dois eventos e mede a página com QR em Chromium, com cache frio, RTT de 150 ms, 1,6 Mbit/s de download e 750 kbit/s de upload. Instale os navegadores da matriz com `npm exec -- playwright install` depois de `npm ci`. O runner fornece os dados sintéticos, o relógio e as portas à suíte e remove a stack descartável ao terminar.

`python3 -B invitation_stack.py --browser --load` valida emissão e substituição do QR do segundo responsável com PostgreSQL, chaves sintéticas e relógio controlado. `--browser` verifica a jornada de emissão e a entrada sem consumo. `--load` mede consulta e emissão com 12 requisições concorrentes e uma consulta sob lock controlado.

`python3 -B invitation_acceptance_stack.py --browser --load` valida prévia e aceite, autorização, corrida pela vaga, borda de expiração, disputa com a trava, rollback e recuperação. `--browser` abre o QR em um contexto independente e recupera uma resposta perdida. `--load` mede prévia e aceite no mesmo convite e em eventos distintos com 12 requisições concorrentes.

## Primeira identificação do convidado

`python3 -B guest_identification_stack.py` sobe uma stack descartável com dois eventos abertos, relógio controlado e aviso sintético. Exercita a primeira identificação por teclado, o retorno, a edição com nova concordância, a troca de categoria e evento, a recuperação com rascunho e os registros locais inválidos. Também cobre link inválido, layout a 320 px e verificações automatizadas de acessibilidade. Em Chromium, mede cinco aberturas com cache frio e rede 4G emulada em cada estado: primeira identificação, retorno e armazenamento indisponível. O runner confere os registros e logs da stack e remove os próprios recursos ao terminar. Prepare os navegadores pelo comando da fundação local.

## Composição e revisão de depoimento em texto

`python3 -B testimonial_stack.py` usa a stack descartável com dois eventos e relógio controlado. Verifica o ingresso pela página pública, a janela, os limites de texto, a revisão, a identificação sintética e o diálogo de descarte. Mede cinco aberturas com cache frio em 4G emulado e registra o tempo de revisão. O runner repete as jornadas de página pública e compartilhamento, confere a persistência e a telemetria e encerra os próprios recursos.

`python3 -B photo_stack.py` acrescenta as jornadas de seleção, troca e remoção de fotos, limites por categoria, legenda, identificação e descarte. Exercita os arquivos de imagem sintéticos em `photos/fixtures`, registra a compatibilidade HEIC por navegador e mede a prontidão e as prévias de 1 e 10 fotos em 4G emulado. Também repete as regressões do depoimento em texto e da página pública.

`python3 -B media_stack.py` exercita seleção e revisão de vídeo e áudio, com fixtures sintéticas em `media/fixtures`. Verifica bordas de duração, compatibilidade de containers e codecs, recuperação, legenda, descarte e privacidade. O runner mede os metadados e a prontidão da prévia em 4G emulado e repete as regressões de fotos, texto e página pública.

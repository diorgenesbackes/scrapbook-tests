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

`python3 -B camera_stack.py` usa a câmera sintética do Chromium para fotografar, refazer e revisar depoimento e momento. Verifica fallback de arquivo com acesso recusado, falha de captura, descarte e encerramento de trilhas. Mede a prontidão e a liberação de recursos em 4G com Android emulado e repete as regressões da F07 e da página pública.

`python3 -B recording_stack.py` usa câmera e microfone sintéticos no Chromium e no Firefox. Exercita gravação e revisão, falhas, descarte, as bordas de 60 e 300 s com relógio controlado e uma gravação real de 60 s em 4G emulado. Verifica o encerramento das trilhas, os arquivos gerados e a ausência de envio. Também repete as regressões de câmera fotográfica, seleção de mídias, texto e página pública.

## Envio e confirmação de depoimento em texto

`python3 -B submission_stack.py` ativa o envio de texto em uma stack descartável com avisos sintéticos, chave efêmera e relógio controlado. Exercita a confirmação transacional, a recuperação com a mesma tentativa, as bordas da janela, o isolamento e a sanitização da telemetria. Prepare os navegadores pelo comando da fundação local e tenha o k6 instalado para medir 200 envios em dois eventos de teste. O runner usa portas de loopback e encerra os próprios recursos.

## Envio e confirmação de depoimento com foto

`python3 -B photo_submission_stack.py` usa PostgreSQL e um S3 versionado local com SQS, AWS CLI e relógio controlado. Envia uma foto sintética pela autorização HTTP emitida pela API e verifica a associação entre evidência de criação, versão validada, original e recibo. Use `--browser` para executar as jornadas de seleção, captura, interrupção e confirmação no Chromium. Use `--load` para medir admissão, autorização e PUT de 200 fotos em dois eventos com k6. O runner cria e encerra recursos próprios em loopback. A comprovação do comportamento da AWS, IAM, KMS e do CORS publicado pertence ao ensaio em homologação.

## Envio de um momento com conjunto de fotos

`python3 -B moment_photo_submission_stack.py` usa as fixtures do envio de foto, PostgreSQL e Moto em loopback. Exercita conjuntos de uma e dez fotos, manifesto inválido, reconhecimento parcial, autorização por arquivo, último recebimento exatamente às 13h, confirmação integral e saneamento por versão após rejeição ou expiração. O runner confere ordem, legenda, recibo e fatos no banco e encerra os recursos que criou.

## Envio e confirmação de depoimento em áudio

`python3 -B audio_submission_stack.py` usa a stack descartável de mídia para validar MP3, M4A, WAV, Ogg e WebM, os limites de tamanho e duração, a recepção às 13h, o original privado e os fatos de confirmação. Acrescente `--with-browser` para selecionar e gravar áudio no Chromium, retomar o envio e conferir o recibo. Prepare o Chromium pelo comando da fundação local.

`python3 -B mixed_submission_load.py --output /private/tmp/scrapbook-f08-carga` usa k6 para medir 200 tentativas em dois eventos sintéticos, com texto, foto, vídeo e áudio. A pasta de saída deve ser nova. Os relatórios da rodada guardam latência por operação da API, transferência, tempo até o recibo, sobreposição das tentativas, uso de recursos e contagens confirmadas no banco.

## Travamento após primeiro registro

`python3 -B event_structural_lock_stack.py` cria uma stack local e descartável com PostgreSQL, SNS e SQS emulados. Confirma texto e mídia, confere a trava estrutural e a auditoria e exercita rejeição, reentrega do fato, corrida com edição e aceite, revogação de convite e política de abertura. Use `--browser` para acrescentar as jornadas Chromium com os eventos do laboratório. `--load --output /private/tmp/scrapbook-f09-carga.json` mede da confirmação à trava persistida em 100 eventos distintos com até 20 solicitações concorrentes. O relógio da aplicação é real nessas rodadas.

`python3 -B event_structural_lock_recovery_stack.py` exercita falha transacional controlada de consumo, DLQ, resultado terminal, retomada autorizada de um fato pelo mesmo `messageId` e deduplicação da entrega recuperada. `--browser` repete o fluxo com a confirmação de recebimento no Chromium e a mudança visível na configuração após a retomada. O runner instala a restrição temporária apenas em PostgreSQL descartável e confere os resultados para a F13 na Outbox.

`python3 -B event_structural_lock_recovery_stack.py --drain` mede a convergência de 20 confirmações após retirar uma espera controlada no consumidor. `--mixed-output /private/tmp/scrapbook-f09-misto` usa o perfil k6 misto da F08 e mede o commit das duas travas distintas. O runner gera as notificações sintéticas de mídia no relógio da API depois de observar cada versão armazenada. A pasta de saída deve ser nova.

## F10-STORY-001 · Preparação de fotos

Com os candidatos de serviço, UI, infraestrutura e Liquibase em diretórios irmãos, execute no repositório de testes

```sh
python3 -B media_processing_stack.py
```

O comando exige Docker com Compose v2, Liquibase, AWS CLI v2, Python 3 com timezone `America/Sao_Paulo` e `ffmpeg`/`ffprobe` no PATH. Ele cria PostgreSQL, Moto com S3 versionado e SNS/SQS, UI e duas réplicas do serviço com portas próprias em loopback. A suíte descarta contêineres, volumes e massa ao terminar. Em Docker alternativo, selecione o contexto local com `DOCKER_CONTEXT`. Se o Docker rodar em VM, use um `TMPDIR` que ela consiga montar para compartilhar o relógio da fixture.

Para incluir as jornadas Playwright da F08 com seleção e câmera emulada, instale as dependências pelo lockfile e execute

```sh
npm ci
npm exec -- playwright install chromium
python3 -B media_processing_stack.py --browser
```

Esse modo confirma os recibos pela interface em Chromium desktop e Android emulado e verifica a prontidão das duas variantes de cada foto após a jornada. O JUnit do navegador fica em `test-results/photo-submissions.xml`. A variante privada é inspecionada no ambiente descartável via PostgreSQL e S3.

| Critério | Verificação automatizada |
| --- | --- |
| CA1, T09 | Recibos F08 preservados, uma admissão por arquivo confirmado de momento e depoimento, zero trabalhos para texto ou momento rejeitado. |
| CA2, T09 | Orientação nos pixels, proporção, limites de 2.048 e 400 px, JPEG decodificável, fundo branco do PNG transparente, HEIC, foto pequena e ausência de EXIF, XMP e IPTC. |
| CA3, T09 | Estado disponível, referências privadas versionadas, escopo consistente por arquivo, checksum e as duas variantes prontas no mesmo commit. |
| CA4, T10 | Duas réplicas, queda depois das duas escritas no S3 com commit bloqueado, reentrega SNS/SQS após reiniciar, uma única dupla promovida e falha definitiva de integridade com correlação para a F13. O original e o recibo permanecem íntegros. |

O relógio controlado avança a lease da tentativa interrompida. O bloqueio de commit e a divergência temporária do checksum são injetados somente no PostgreSQL descartável. A verificação cobre o armazenamento e a mensageria locais emulados.

### Preparação de áudio da F10 em laboratório

Execute `TMPDIR=/private/tmp python3 -B audio_media_processing_stack.py` com Docker e FFmpeg/ffprobe disponíveis. Acrescente `--browser` após instalar as dependências do Playwright e Chromium para executar as jornadas selecionadas de envio WebM e retomada. A suíte sobe stack descartável em loopback com PostgreSQL, S3 versionado, SNS/SQS e a interface HTTP de contrato do MediaConvert. Usa entradas sintéticas MP3, M4A, WAV, OGG e WebM aceitas pela F08 e publica uma saída M4A/AAC real de referência na fixture. Exercita recibo anterior ao processamento, perda de resposta após `CreateJob`, evento antecipado e duplicado, saída inválida e integridade da variante privada. A verificação do provedor gerenciado e de suas permissões ocorre em homologação.

### Preparação privada de vídeo da F10

Execute `python3 -B video_media_processing_stack.py` para validar a F08 e a F10 com PostgreSQL, S3 versionado, SNS/SQS e uma fixture local do contrato do MediaConvert. O runner gera amostras sintéticas MP4, MOV e WebM e referências H.264/AAC verificáveis com FFmpeg no host. Exercita rotação, aspecto anamórfico, VFR, limite de resolução, saída muda ou sonora, resultado sem objeto, resposta perdida, reentrega após prontidão e saída inválida. Confira o comportamento real da conversão e dos eventos separadamente em homologação AWS.

Use `--browser` para incluir os envios de MOV e WebM e verificar as variantes prontas no banco após os recibos. O laboratório cria recursos descartáveis em loopback. Consulte o [runner de vídeo F08](video_submission_stack.py) para os pré-requisitos do navegador.

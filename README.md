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

`python3 -B event_public_stack.py --browser --load` valida a página dos convidados com eventos sintéticos e relógio controlado. O comando sem flags roda HTTP e PostgreSQL, incluindo edição da F03, prazos, campos públicos, headers e telemetria. `--browser` acrescenta Playwright na matriz local. `--load` roda k6 com 200 convidados em dois eventos e mede a página em Chromium com cache frio, RTT de 150 ms, 1,6 Mbit/s de download e 750 kbit/s de upload. Instale os navegadores da matriz com `npm exec -- playwright install` depois de `npm ci`. O runner fornece as portas, os códigos sintéticos e o relógio à suíte e remove a stack descartável ao terminar.

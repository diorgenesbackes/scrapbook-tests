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

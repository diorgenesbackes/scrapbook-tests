# Scrap Book — Testes não unitários

Repositório reservado às suítes HTTP, integração com PostgreSQL, navegador, segurança, carga e recuperação do Scrap Book, conforme o [HLD](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/HLD.md) e os [guidelines](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/guidelines/README.md).

## Estado atual

A preparação de Gestão de Eventos inclui runner Python com biblioteca padrão, probes .NET 10 e Playwright **1.65.0** para Chromium/WebKit. Os cenários funcionais completos continuam sendo implementados conforme o [FDD](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/fdd/FDD-Gestao-de-Eventos/FDD-Gestao-de-Eventos.md) e o [planejamento](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/fdd/FDD-Gestao-de-Eventos/1-planejamento.md). Não há k6 instalado nem execução automática em CI.

Os unitários permanecem em `scrapbook-service` e `scrapbook-ui`. O projeto Liquibase mantém suas próprias validações de migrations.

## Execução e evidências

Com os repositórios clonados como diretórios irmãos e o PostgreSQL local iniciado:

```bash
npm ci
npx playwright install chromium webkit
dotnet restore probes/ScrapBook.Probes.csproj --locked-mode
python3 run.py preflight
python3 run.py foundation
python3 run.py keys
```

`foundation` verifica o papel DML, tentativas de DDL negadas, evento/membro/auditoria em transação desfeita, repetição/reversão do bootstrap e colisão de conta sintética. Preserva as 33 tabelas/47 migrations e os dados fora da massa do teste. `keys` recria o provider com o mesmo diretório privado e confere a leitura do cursor e permissões. O runner rejeita destinos HTTP fora de loopback antes de abrir conexão.

Os comandos precisam de Docker e SDK .NET 10/Node 26.13.0/npm 11.20.0. Playwright tem perfis Chromium desktop e WebKit móvel emulado, sem equivalência a validação em aparelhos físicos. Relatórios, traces e navegadores instalados não entram no Git.

As suítes deste repositório terão acionamento manual; não serão executadas automaticamente em merge, deploy ou startup da aplicação. Documentar cada comando ao implementar o respectivo runner.

Usar fixtures sintéticas, destino explicitamente validado e limpeza restrita à própria massa. Registrar ambiente, revisão, comandos, resultado, falhas e testes não executados nos ciclos de `scrapbook-docs/testing/`, quando existirem.

## Dependências e dados locais

Versionar somente código de testes, configuração sem segredos, fixtures seguras, manifests e lockfiles. Instalar dependências pelas ferramentas escolhidas; não adicionar bibliotecas instaladas, ambientes virtuais, binários, relatórios brutos, credenciais ou conteúdo real de convidados.

O `.gitignore` cobre dependências e saídas comuns de Node, Python e .NET para a futura implementação, sem afirmar que essas ferramentas já foram adotadas.

## Contribuição

A branch principal é `main`. Seguir o [guia de contribuição](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/AGENTS.md) e o [Git Flow](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/guidelines/git-flow.md). Versionar manifests e lockfiles; dependências instaladas, artefatos gerados e segredos permanecem locais.

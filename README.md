# Scrap Book — Testes não unitários

Repositório reservado às suítes HTTP, integração com PostgreSQL, navegador, segurança, carga e recuperação do Scrap Book, conforme o [HLD](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/HLD.md) e os [guidelines](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/guidelines/README.md).

## Estado atual

Ainda não há suíte executável nem framework instalado. Os cenários de Gestão de Eventos estão previstos no [FDD](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/fdd/FDD-Gestao-de-Eventos/FDD-Gestao-de-Eventos.md) e no [planejamento](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/fdd/FDD-Gestao-de-Eventos/1-planejamento.md). Playwright e k6 continuam planejados; ferramentas e versões serão fixadas ao implementar os testes.

Os unitários permanecem em `scrapbook-service` e `scrapbook-ui`. O projeto Liquibase mantém suas próprias validações de migrations.

## Execução e evidências

As suítes deste repositório terão acionamento manual; não serão executadas automaticamente em merge, deploy ou startup da aplicação. Documentar cada comando ao implementar o respectivo runner.

Usar fixtures sintéticas, destino explicitamente validado e limpeza restrita à própria massa. Registrar ambiente, revisão, comandos, resultado, falhas e testes não executados nos ciclos de `scrapbook-docs/testing/`, quando existirem.

## Dependências e dados locais

Versionar somente código de testes, configuração sem segredos, fixtures seguras, manifests e lockfiles. Instalar dependências pelas ferramentas escolhidas; não adicionar bibliotecas instaladas, ambientes virtuais, binários, relatórios brutos, credenciais ou conteúdo real de convidados.

O `.gitignore` cobre dependências e saídas comuns de Node, Python e .NET para a futura implementação, sem afirmar que essas ferramentas já foram adotadas.

## Contribuição

A branch principal é `main`. Seguir o [guia de contribuição](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/AGENTS.md) e o [Git Flow](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/guidelines/git-flow.md). Versionar manifests e lockfiles; dependências instaladas, artefatos gerados e segredos permanecem locais.

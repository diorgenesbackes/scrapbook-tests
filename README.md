# Scrap Book — Testes não unitários

Repositório reservado às suítes HTTP, integração com PostgreSQL, navegador, segurança, carga e recuperação do Scrap Book, conforme o [HLD](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/HLD.md) e os [guidelines](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/guidelines/README.md).

## Estado atual

A feature `GE-STORY-001` inclui Python 3.13.15/biblioteca padrão, probes .NET e Playwright **1.65.0**. Implementa contratos HTTP, isolamento, concorrência, falhas transacionais, jornadas de navegador e perfil de carga H-03. Não há k6 instalado nem execução automática em CI. A branch não foi promovida/publicada.

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

As suítes são manuais; não são executadas em merge, deploy ou startup da aplicação.

## Jornadas de Events em banco descartável

Restaurar/compilar `scrapbook-service` e executar `npm run build` em `scrapbook-ui` antes. Instalar os requisitos acima, Liquibase 4.33.0/Java e iniciar Docker local. A partir deste repositório:

```bash
python3 journeys.py models
python3 journeys.py backend
python3 journeys.py browser
python3 journeys.py load
python3 journeys.py all
```

`event_lab.py` exige socket Unix local, cria container/portas loopback exclusivos e aplica as mesmas 47 migrations com migrador sem superusuário. Prepara runtime DML e três contas sintéticas. APIs usam contas fixas por configuração; nenhuma credencial vai para argumentos, manifesto de navegador ou relatório. O container descartável e processos são encerrados ao final; o Compose/volume persistente do usuário não é alterado.

- `backend`: contratos, paginação, conta alheia/segundo responsável, rollback antecipado/diferível, gatilhos, transições e obrigações antigas; depois indisponibilidade/lock, orçamento compartilhado e privacidade.
- `browser`: contratos HTTP + 14 jornadas em Chromium desktop e WebKit/iPhone emulado, com fusos Tokyo/Los Angeles, build de produção servido por preview e proxy para a API do laboratório.
- `load`: completa a massa em 200 eventos, aquece três ciclos por worker e executa 10 concorrentes por 60s. Cada worker atualiza evento próprio com versão válida; mede lista/detalhe/update separadamente, exige zero erros e p95 ≤500ms.
- `all`: executa todas essas etapas na mesma base exclusiva; relatório sanitizado em `artifacts/all-report.json`. Saídas brutas e capturas ficam ignoradas; evidência revisada é versionada em scrapbook-docs.

As falhas injetadas, funções/triggers temporários e snapshot estrutural travado existem somente nesse laboratório. Não representam implementação de Records/consumidor ou convites. O cleanup elimina o container exclusivo inteiro, sem tentar apagar eventos/auditoria em uma base real. Interromper o runner permite o bloco finally executar; após kill abrupto, conferir/remover somente o container `scrapbook-events-test-<identificador>` da execução conhecida.

O teste temporal usa relógio real do PostgreSQL com eventos já encerrados e valida janela 12h/13h; os instantes exatos antes/no/depois do meio-dia são cobertos por unitários substituíveis, sem afirmar execução de integração durante o minuto real de 12h. Emulação não substitui aparelhos físicos, leitor de tela ou auditoria WCAG completa. H-03 não mede os 200 uploads do PRD nem infraestrutura AWS.

Usar fixtures sintéticas, destino explicitamente validado e limpeza restrita à própria massa. Registrar ambiente, revisão, comandos, resultado, falhas e testes não executados nos ciclos de `scrapbook-docs/testing/`, quando existirem.

## Dependências e dados locais

Versionar somente código de testes, configuração sem segredos, fixtures seguras, manifests e lockfiles. Instalar dependências pelas ferramentas escolhidas; não adicionar bibliotecas instaladas, ambientes virtuais, binários, relatórios brutos, credenciais ou conteúdo real de convidados.

O `.gitignore` cobre dependências e saídas comuns de Node, Python e .NET para a futura implementação, sem afirmar que essas ferramentas já foram adotadas.

## Contribuição

A branch principal é `main`. Seguir o [guia de contribuição](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/AGENTS.md) e o [Git Flow](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/guidelines/git-flow.md). Versionar manifests e lockfiles; dependências instaladas, artefatos gerados e segredos permanecem locais.

## Consumidores após RF-STORY-001

Os probes usam os namespaces por conceito e o ScrapBookDbContext único. O inventário esperado passa a cinco mapeamentos (quatro Events e conta Identity); autorização é conferida por IEventRepository. `journeys.py models` executa o probe em banco descartável, sem precisar iniciar o Compose do usuário. backend/all também incluem esse probe. A suíte verifica os mesmos contratos após a migração para Controllers MVC. No complemento de isolamento, Models/Enums/Builders/Requests/Responses e entidades POCO passam a ScrapBook.Domain; probes usam Domain.Entities. DbContext/configurações EF continuam em Infrastructure. Services são exclusivos de Application, e os adaptadores técnicos permanecem nas camadas externas.

## Stack inteira em containers

Após construir as imagens com `python3 scripts/stack_local.py build` em infrastructure, executar manualmente `python3 docker_stack.py` neste repositório. Requer Docker Compose, Liquibase/Java e Playwright instalados conforme os requisitos acima.

A suíte usa o compose.yaml real com projeto aleatório, credenciais/portas/rede/volumes próprios, aplica as mesmas 47 migrations e prepara runtime DML/fixture sintética. Confere identidade desativada por padrão, DNS interno, API/UI sem root e filesystem somente leitura, ausência de SDK/Node no runtime, health direto/proxy, SPA/assets, JSON/versão/auditoria, dados e cursor após down/up, 14 jornadas e H-03 através de Nginx→API→PostgreSQL. Não usa o Compose/volume persistente do usuário.

Relatório sanitizado: artifacts/docker-stack-report.json. Diagnóstico privado de comando que falhou: artifacts/docker-stack-private.log; logs recentes dos containers: artifacts/docker-stack-containers-private.log. Nunca versionar esses diagnósticos. A carga imprime métricas agregadas antes de aplicar os critérios de aceite, inclusive quando falhar. Cleanup remove somente os volumes do projeto scrapbook-docker-test-<identificador>. O uso de down --volumes pertence exclusivamente a esse laboratório; a parada habitual preserva volumes.

## Catálogo de identidade visual

Execução manual: `npx playwright test --config visual-identity.config.ts`. O runner inicia/encerra um servidor HTTP exclusivo em loopback:4175 que serve somente scrapbook-docs/visual-identity. Não usa API, banco ou stack persistente.

Cobre inventário FR-001–FR-022, 38 telas/178 estados em 320/1440px e Chromium/WebKit, layouts representativos em 320/390/768/1440px, proporções de mídia, assets, teclado/foco/abas/diálogo, proteção de bloqueio/recibo, alteração de nome/concordância e contadores. Inclui texto 200% e reduced motion. Relatório JSON e capturas em artifacts/visual-identity*, ignorados pelo Git; evidências selecionadas/revisadas ficam na documentação.

A suíte não comprova acessibilidade integral, leitor de tela, aparelhos físicos, usabilidade, Cognito ou captura/upload/reprodução reais. A proposta visual depende da revisão do responsável antes do aceite.

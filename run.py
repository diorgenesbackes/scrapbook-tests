#!/usr/bin/env python3
"""Acionamento manual; alvos HTTP são exclusivamente loopback."""
import argparse
import concurrent.futures
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def local_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {'', '/'}):
        raise argparse.ArgumentTypeError('Destino deve ser HTTP loopback, sem credencial, path ou query.')
    try:
        port = parsed.port
    except ValueError as error:
        raise argparse.ArgumentTypeError('Porta inválida.') from error
    if port is None or not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError('Porta local explícita entre 1024 e 65535 é obrigatória.')
    return value.rstrip('/')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=['preflight', 'keys', 'foundation', 'health-load', 'models'])
    parser.add_argument('--base-url', type=local_url, default='http://127.0.0.1:5080')
    parser.add_argument('--duration', type=int, default=5)
    args = parser.parse_args()
    if args.suite == 'preflight':
        for command in [['dotnet', '--version'], ['node', '--version'], ['npm', '--version'], ['docker', 'version', '--format', '{{.Server.Version}}']]:
            result = subprocess.run(command, text=True, capture_output=True, check=True)
            print(json.dumps({'tool': command[0], 'version': result.stdout.strip()}))
        print(json.dumps({'target': args.base_url, 'local': True}))
    elif args.suite == 'keys':
        subprocess.run(['dotnet', 'run', '--project', str(ROOT / 'probes'), '--no-restore'], check=True)
    elif args.suite == 'foundation':
        from foundation import run
        run()
    elif args.suite == 'models':
        sys.path.insert(0, str(ROOT.parent / 'scrapbook-infrastructure/scripts'))
        import events_local
        config = events_local.configuration()
        events_local.require_local_docker()
        actor = json.loads((events_local.STATE / 'actors.json').read_text())['owner']['accountId']
        connection = ('Host=127.0.0.1;Port=' + config['POSTGRES_PORT'] + ';Database=' + config['POSTGRES_DB']
                      + ';Username=' + config['POSTGRES_RUNTIME_USER'] + ';Password=' + config['POSTGRES_RUNTIME_PASSWORD']
                      + ';Timeout=2;Command Timeout=4;Include Error Detail=false')
        env = dict(os.environ, ConnectionStrings__ScrapBook=connection, SCRAPBOOK_PROBE_ACCOUNT_ID=actor)
        subprocess.run(['dotnet', 'run', '--project', str(ROOT / 'probes'), '--no-restore', '--', 'models'], env=env, check=True)
    else:
        if not 1 <= args.duration <= 120:
            raise ValueError('Duração deve estar entre 1 e 120 segundos.')
        # Baseline operacional; NÃO representa o perfil de Events do FDD.
        for _ in range(10):
            with urllib.request.urlopen(args.base_url + '/health', timeout=4) as response:
                assert response.status == 200
        deadline = time.monotonic() + args.duration
        def worker(_):
            latencies, errors = [], 0
            while time.monotonic() < deadline:
                start = time.monotonic()
                try:
                    with urllib.request.urlopen(args.base_url + '/health', timeout=4) as response:
                        assert response.status == 200 and json.load(response) == {'status': 'up'}
                except (OSError, ValueError, AssertionError):
                    errors += 1
                latencies.append((time.monotonic() - start) * 1000)
            return latencies, errors
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(worker, range(10)))
        latencies = sorted(x for samples, _ in results for x in samples)
        errors = sum(errors for _, errors in results)
        print(json.dumps({'profile': 'health_baseline_only', 'seconds': args.duration, 'concurrency': 10,
                          'requests': len(latencies), 'errors': errors,
                          'p95_ms': round(latencies[math.ceil(len(latencies) * .95) - 1], 3)}))
        assert errors == 0
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(type(error).__name__ + ': suíte manual falhou; confira o comando e pré-requisitos.', file=sys.stderr)
        sys.exit(1)

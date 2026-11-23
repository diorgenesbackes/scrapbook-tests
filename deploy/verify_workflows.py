import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

FIXTURE = r'''
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
env = os.environ
case = env["FIXTURE"]
recovering = case.startswith("recover-")
component = env["COMPONENT"]
digest = "sha256:" + "b" * 64
account = "123456789012"
base = "arn:aws:ecs:us-east-1:" + account + ":task-definition/"
definition = base + component + ":2"
branch = env["GITHUB_REF_NAME"]
target = "production" if branch == "main" else "hlg"
production = target == "production"
source_branch = "hlg" if case == "recover-other-environment" else branch
source_target = "hlg" if case in ("recover-other-environment", "recover-other-target") else target
pending_name = f"/scrapbook/{target}/liquibase/pending"
active_name = f"/scrapbook/{target}/liquibase/active"
pending_path = Path("pending.json")
calls_path = Path("calls.jsonl")
previous = calls_path.read_text().splitlines() if calls_path.exists() else []
with calls_path.open("a") as out:
    out.write(json.dumps([tool, *args[:2]]) + "\n")
def output(value):
    print(json.dumps(value) if not isinstance(value, str) else value)
def refuse():
    raise SystemExit(61)

if tool == "gh":
    if recovering and args[:2] == ["run", "download"]:
        destination = Path(args[args.index("--dir") + 1])
        destination.mkdir(exist_ok=True)
        repository = env["GITHUB_REPOSITORY"] if case != "recover-wrong-evidence" else "another/repository"
        image = f"{account}.dkr.ecr.us-east-1.amazonaws.com/scrapbook-{component}@{digest}"
        (destination / "release-evidence.json").write_text(json.dumps({
            "sha": "f" * 40, "version": "v0.9.1", "result": "success", "environment": source_target, "branch": source_branch,
            "repository": repository, "runId": "91", "attempt": "2",
            "databaseGeneration": "unexpected" if case == "recover-wrong-generation" else env["DATABASE_GENERATION"],
            "activeImage": image, "image": image, "archiveSha256": env.get("SITE_SHA256"),
            "uiOrigin": env["UI_ORIGIN"], "apiOrigin": env["VITE_API_BASE_URL"],
        }))
    elif recovering and "/attempts/" in args[1]:
        output({"head_sha": "f" * 40, "head_branch": source_branch, "event": "push", "id": 91, "run_attempt": 2,
                "head_repository": {"full_name": env["GITHUB_REPOSITORY"]},
                "path": ".github/workflows/publish.yml" if production and case != "recover-legacy-path" else ".github/workflows/publish-hlg.yml",
                "conclusion": "success" if case != "recover-failed-source" else "failure"})
    else:
        assert args[1] == f'repos/{env["GITHUB_REPOSITORY"]}/git/ref/heads/{branch}', args
        output("a" * 40 if case != "stale" else "f" * 40)
elif tool == "docker":
    if args[0] == "login":
        assert sys.stdin.read() == "mock-token\n"
    if case == "build-failure" and args[0] == "build":
        refuse()
elif tool == "npm":
    assert "AWS_SESSION_TOKEN" not in env and "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in env
    if args == ["run", "build"]:
        Path("dist").mkdir()
        Path("dist/index.html").write_text("<html/>")
elif tool == "curl":
    if "--config" in args:
        assert "https://signed.example.test/private-test-url" in sys.stdin.read()
    else:
        output({"sha": env.get("SITE_SHA", "a" * 40), "version": env.get("SITE_VERSION", "v1.0.0")})
elif tool == "sleep":
    assert args == ["30"]
elif tool == "aws":
    group, action = args[:2]
    key = (group, action)
    if key == ("ssm", "get-parameter"):
        assert args[args.index("--name") + 1] == active_name
        if case == "no-database":
            refuse()
        marker = {"environment": "hlg" if case == "other-environment-marker" else target, "result": "success", "version": "v1.3.7", "sha": "c" * 40,
                  "generation": "f01-story-001-base" if case != "wrong-generation" else "unexpected"}
        if case == "missing-marker-provenance":
            del marker["version"]
            del marker["sha"]
        if case == "malformed-marker-provenance":
            marker.update(version="v01.1.0", sha="c" * 39)
        output(marker)
    elif key == ("ssm", "get-parameters"):
        assert args[args.index("--names") + 1] == pending_name
        if case in ("pending-running", "pending-stopped", "pending-no-arn", "pending-other-environment"):
            pending = {"cluster": env["ECS_CLUSTER"], "taskDefinition": definition,
                       "image": "registry/liquibase@" + digest, "version": "v1.0.0",
                       "environment": "hlg" if case == "pending-other-environment" else target, "result": "incomplete", "sha": "f" * 40,
                       "generation": "f01-story-001-base"}
            if case != "pending-no-arn":
                pending["task"] = "previous-task"
            pending_path.write_text(json.dumps(pending))
        output({
            "Parameters": [{"Name": pending_name, "Value": pending_path.read_text()}] if pending_path.exists() else [],
            "InvalidParameters": [] if pending_path.exists() else [pending_name],
        })
    elif key == ("ecs", "describe-services"):
        activated = json.dumps(["aws", "ecs", "update-service"]) in previous
        desired = (2 if production else 1) if activated or recovering else int(case == "underprovisioned")
        current = ":9" if case == "recover-wrong-active-task" else ":1"
        output({"services": [{"status": "ACTIVE", "deploymentController": {"type": "ECS"},
                "taskDefinition": definition if activated else base + component + current, "runningCount": desired if activated else 0,
                "availabilityZoneRebalancing": "DISABLED" if case == "zone-rebalancing-off" else "ENABLED",
                "desiredCount": desired, "deployments": [{}] if activated else [],
                "loadBalancers": [{"targetGroupArn": "target-arn", "containerName": "api", "containerPort": 8080}]}]})
    elif key == ("elbv2", "describe-target-groups"):
        output({"TargetGroups": [{"TargetType": "ip", "HealthCheckPath": "/health/ready" if case != "wrong-health" else "/health",
                 "HealthCheckPort": "traffic-port", "Matcher": {"HttpCode": "200"}}]})
    elif key == ("ecs", "describe-task-definition"):
        environment = [{"name": name, "value": value} for name, value in [
            ("ASPNETCORE_ENVIRONMENT", "Production" if production else "Homologation"),
            ("DevelopmentIdentity__Enabled", "true" if case == "unsafe-local-identity" else "false"),
            ("Hosting__PublicUiOrigin", env["UI_ORIGIN"]),
            ("Database__RequireTls", "true")]] if component == "api" else [
            {"name": "LIQUIBASE_COMMAND_URL",
             "value": env.get("MIGRATION_DATABASE_URL", "jdbc:fixture") if case != "wrong-migration-url" else "jdbc:other"}]
        secret_name = "ConnectionStrings__ScrapBook" if component == "api" else "LIQUIBASE_COMMAND_PASSWORD"
        secret_arn = env.get("DATABASE_SECRET_ARN" if component == "api" else "MIGRATION_PASSWORD_SECRET_ARN",
                             "arn:aws:secretsmanager:us-east-1:fixture")
        if case == "wrong-secret":
            secret_arn = f"arn:aws:secretsmanager:us-east-1:{account}:secret:other"
        if recovering and component == "liquibase":
            endpoint = "fixture.rds.amazonaws.com" if case != "recover-wrong-task-url" else "other.rds.amazonaws.com"
            environment = [{"name": "LIQUIBASE_COMMAND_URL", "value": f"jdbc:postgresql://{endpoint}:5432/scrapbook_recovery?sslmode=verify-full&sslrootcert=/opt/scrapbook/certs/rds-us-east-1.pem"}]
        image = f"{account}.dkr.ecr.us-east-1.amazonaws.com/scrapbook-{component}@{digest}" if recovering else "base"
        output({"taskDefinition": {"family": component, "status": "ACTIVE", "networkMode": "awsvpc",
                 "taskDefinitionArn": definition,
                 "requiresCompatibilities": ["FARGATE"], "containerDefinitions": [{"name": component,
                 "essential": True, "environment": environment, "image": image,
                 "command": ["fixture-override"] if case == "unsafe-command" else [],
                 "logConfiguration": {"logDriver": "awslogs"},
                 "secrets": [{"name": secret_name, "valueFrom": secret_arn}]}]}})
    elif key == ("ecr", "describe-repositories"):
        output("MUTABLE" if case == "mutable" else "IMMUTABLE")
    elif key == ("ecr", "get-login-password"):
        output("mock-token")
    elif key == ("ecr", "describe-images"):
        output(digest if case != "recover-digest-mismatch" else "sha256:" + "f" * 64)
    elif key == ("ecs", "register-task-definition"):
        value = json.load(sys.stdin)
        assert value["containerDefinitions"][0]["image"].endswith("@" + digest)
        assert "status" not in value
        output(definition)
    elif key == ("ecs", "update-service"):
        assert args[args.index("--task-definition") + 1] == definition
        if recovering:
            assert "--desired-count" not in args
        else:
            assert args[args.index("--desired-count") + 1] == ("2" if production else "1")
    elif key == ("ecs", "list-tasks"):
        output({"taskArns": ["task-api", "task-api-secondary"] if production else ["task-api"]})
    elif key == ("ecs", "run-task"):
        assert args[args.index("--task-definition") + 1] == definition
        if recovering:
            overrides = json.loads(args[args.index("--overrides") + 1])
            assert overrides["containerOverrides"][0]["command"] == [env["RECOVERY_OPERATION"]]
        else:
            pending = json.loads(pending_path.read_text())
            assert pending["clientToken"] == args[args.index("--client-token") + 1]
            assert pending["clientToken"] == args[args.index("--started-by") + 1]
        output({"failures": [], "tasks": [{"taskArn": "task-migration"}]})
    elif key == ("ecs", "describe-tasks"):
        tasks = args[args.index("--tasks") + 1:args.index("--output")]
        running = component == "api" or case in ("pending-running", "migration-timeout-running")
        output({"failures": [], "tasks": [
            {"taskArn": task, "taskDefinitionArn": definition,
             "availabilityZone": "us-east-1b" if index and case != "same-zone" else "us-east-1a",
             "lastStatus": "RUNNING" if running else "STOPPED",
             "attachments": [{"details": [{"name": "privateIPv4Address", "value": f"10.0.0.{42 + index}"}]}],
             "containers": [{"name": component, "imageDigest": digest if case != "different-digest" else "sha256:changed",
                             "exitCode": 1 if case == "migration-failure" else 0}]}
            for index, task in enumerate(tasks)
        ]})
    elif key == ("elbv2", "describe-target-health"):
        addresses = ["10.0.0.77" if case == "other-healthy-target" else "10.0.0.42"]
        if production:
            addresses.append("10.0.0.43")
        output({"TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"},
                 "Target": {"Id": address, "Port": 8080}} for address in addresses]})
    elif key == ("ssm", "put-parameter"):
        name = args[args.index("--name") + 1]
        marker = json.loads(args[args.index("--value") + 1])
        if name == pending_name:
            if case == "concurrent-migration" or pending_path.exists() and "--overwrite" not in args:
                refuse()
            pending_path.write_text(json.dumps(marker))
        else:
            assert name == active_name
            assert marker["environment"] == target
            if case == "migration-marker-write-failure":
                refuse()
            assert marker["result"] == "success" and marker["version"] == "v1.0.0"
            Path("active.json").write_text(json.dumps(marker))
    elif key == ("ssm", "delete-parameter"):
        assert args[args.index("--name") + 1] == pending_name
        assert Path("active.json").exists()
        pending_path.unlink()
    elif key == ("amplify", "create-deployment"):
        output({"jobId": "42", "zipUploadUrl": "https://signed.example.test/private-test-url"})
    elif key == ("amplify", "list-jobs"):
        output("41" if case != "recover-wrong-amplify-job" else "40")
    elif key == ("rds", "describe-db-instances"):
        purpose = "scrapbook-recovery-rehearsal" if case != "recover-shared-database" else "shared"
        output({"DBInstances": [{"DBInstanceArn": f"arn:aws:rds:us-east-1:{account}:db:fixture-db",
                "DBInstanceStatus": "available", "Engine": "postgres", "PubliclyAccessible": False,
                "TagList": [{"Key": "Environment", "Value": "hlg"}, {"Key": "Purpose", "Value": purpose}],
                "Endpoint": {"Port": 5432, "Address": "fixture.rds.amazonaws.com"}}]})
    elif key == ("amplify", "get-job"):
        output("FAILED" if case == "amplify-failure" else "SUCCEED")
    elif key == ("ecs", "wait") and case.startswith("migration-timeout-"):
        raise SystemExit(255)
    elif key in (("ecs", "wait"), ("amplify", "start-deployment")):
        pass
    else:
        raise AssertionError(key)
'''

scripts = {"api": "publish_ecs.sh", "ui": "publish_amplify.sh", "liquibase": "publish_migration.sh"}
recovery_scripts = {"api": "recover_ecs.sh", "ui": "recover_amplify.sh", "liquibase": "recover_migration.sh"}

def verify(roots, component, case, success, forbidden=None, *, branch="hlg"):
    recovering = case.startswith("recover-")
    with TemporaryDirectory(prefix="scrapbook-aws-fixture-") as directory:
        work = Path(directory)
        (work / "bin").mkdir()
        for command in ("aws", "docker", "gh", "npm", "curl", "sleep"):
            path = work / "bin" / command
            path.write_text("#!/usr/bin/env python3\n" + FIXTURE)
            path.chmod(0o700)
        shutil.copytree(roots[component] / ".github/scripts", work / ".github/scripts")
        if component == "liquibase":
            (work / "config").mkdir()
            shutil.copyfile(roots[component] / "config/catalog-generation.txt", work / "config/catalog-generation.txt")
        local_env = {key: value for key, value in os.environ.items()
                     if not key.startswith(("ACTIONS_ID_TOKEN_", "AWS_", "GH_", "GITHUB_", "VITE_"))}
        env = local_env | {
            "PATH": f"{work / 'bin'}:{os.environ['PATH']}",
            "AWS_ACCOUNT_ID": "123456789012", "ECR_REPOSITORY": f"scrapbook-{component}",
            "GITHUB_SHA": "a" * 40, "GITHUB_REPOSITORY": f"scrapbook/{component}", "GITHUB_REF_NAME": branch,
            "GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1", "RELEASE_VERSION": "v1.0.0",
            "RESERVED_RUN_KEY": "100.1", "GITHUB_OUTPUT": str(work / "github-output"),
            "GH_TOKEN": "local-fixture-token", "COMPONENT": component, "FIXTURE": case,
            "ECS_CLUSTER": "cluster", "ECS_SERVICE": "service", "ALB_TARGET_GROUP_ARN": "target-arn",
            "DATABASE_GENERATION": "f01-story-001-base", "MIGRATION_TASK_DEFINITION": "liquibase:1",
            "MIGRATION_SUBNET_IDS": '["subnet-aaa", "subnet-bbb"]', "MIGRATION_GROUP_ID": "sg-abc",
            "AMPLIFY_APP_ID": "abc42", "AMPLIFY_BRANCH": "hlg", "UI_ORIGIN": "https://ui.hlg.example.test",
            "VITE_API_BASE_URL": "https://api.hlg.example.test",
        }
        if branch == "main":
            prefix = f'arn:aws:secretsmanager:us-east-1:{env["AWS_ACCOUNT_ID"]}:secret:'
            env.update(AMPLIFY_BRANCH="hlg" if case == "wrong-amplify-branch" else "main",
                       UI_ORIGIN="https://ui.example.test", VITE_API_BASE_URL="https://api.example.test",
                       DATABASE_SECRET_ARN=prefix + "production-api",
                       MIGRATION_PASSWORD_SECRET_ARN=prefix + "production-liquibase",
                       MIGRATION_DATABASE_URL="jdbc:postgresql://production.rds.amazonaws.com:5432/scrapbook?sslmode=verify-full&sslrootcert=/opt/scrapbook/certs/rds-us-east-1.pem")
        if case == "partial-retry":
            env["GITHUB_RUN_ATTEMPT"] = "2"
        if recovering:
            account = env["AWS_ACCOUNT_ID"]
            base = f"arn:aws:ecs:us-east-1:{account}"
            env.update(SOURCE_RUN_ID="91", SOURCE_ATTEMPT="2", SOURCE_SHA="f" * 40, SOURCE_VERSION="v0.9.1",
                       RECOVERY_EVIDENCE=str(work / "source/release-evidence.json"),
                       EXPECTED_TASK=f"{base}:task-definition/api:1", EXPECTED_CURRENT_JOB="41",
                       RECOVERY_CLUSTER=f"{base}:cluster/recovery", RECOVERY_TASK_DEFINITION=f"{base}:task-definition/liquibase:2",
                       RECOVERY_RDS_IDENTIFIER="fixture-db", RECOVERY_SUBNET_IDS=env["MIGRATION_SUBNET_IDS"],
                       RECOVERY_GROUP_ID="sg-abc", RECOVERY_OPERATION="preview-empty-base")
            if case == "recover-rollback":
                env["RECOVERY_OPERATION"] = "rollback-empty-base"
        if component == "ui" and case != "partial-retry":
            build_env = {key: value for key, value in env.items() if not key.startswith(("AWS_", "GH_"))}
            if case == "wrong-archive-revision":
                build_env["GITHUB_SHA"] = "e" * 40
            if recovering:
                build_env.update(GITHUB_SHA=env["SOURCE_SHA"], RELEASE_VERSION=env["SOURCE_VERSION"])
            build = subprocess.run(["bash", ".github/scripts/build_amplify.sh"], cwd=work, env=build_env,
                                   text=True, capture_output=True, timeout=30)
            assert build.returncode == 0, build
            outputs = dict(line.split("=", 1) for line in (work / "github-output").read_text().splitlines())
            env.update(SITE_ARCHIVE=str(work / "site.zip"), SITE_SHA256=outputs["sha256"],
                       SITE_API_ORIGIN=outputs["api_origin"], SITE_UI_ORIGIN=outputs["ui_origin"],
                       AWS_SESSION_TOKEN="mock-deployment-token")
            if case in ("tampered-archive", "recover-tampered-archive"):
                (work / "site.zip").write_bytes(b"arquivo alterado")
            if case == "changed-archive-origin":
                env["VITE_API_BASE_URL"] = "https://other.hlg.example.test"
            if case == "changed-ui-archive-origin":
                env["UI_ORIGIN"] = "https://other.hlg.example.test"
        command = f'source .github/scripts/release_support.sh && start_evidence && bash .github/scripts/{scripts[component]}'
        if recovering:
            entry = "source .github/scripts/recovery_support.sh && start_recovery_evidence"
            if component != "liquibase":
                entry += ' && fetch_recovery_evidence source'
            command = f'source .github/scripts/release_support.sh && {entry} && bash .github/scripts/{recovery_scripts[component]}'
        elif component == "ui":
            command = command.replace("&& bash", "&& bash .github/scripts/verify_amplify_archive.sh && bash", 1)
        result = subprocess.run(["bash", "-c", command], cwd=work, env=env, text=True, capture_output=True, timeout=30)
        assert (result.returncode == 0) == success, (component, case, result)
        assert "private-test-url" not in result.stdout + result.stderr
        calls_path = work / "calls.jsonl"
        calls = calls_path.read_text() if calls_path.exists() else ""
        if case == "partial-retry":
            assert not calls and "Reexecute todos os jobs" in result.stderr, result
        if forbidden:
            assert forbidden not in calls, (case, calls)
        if success:
            evidence = json.loads((work / "release-evidence.json").read_text())
            assert evidence["branch"] == branch
            assert evidence["environment"] == ("production" if branch == "main" else "hlg")
            if component == "api" and branch == "main":
                assert evidence["runningTasks"] == "2" and evidence["availabilityZones"] == "us-east-1a,us-east-1b"
            assert ("activeImage" if component == "api" else "generation" if component == "liquibase"
                    else "archiveSha256") in evidence
        if component == "liquibase" and not recovering:
            active = work / "active.json"
            pending = work / "pending.json"
            assert active.exists() == success
            assert pending.exists() == (not success and case not in ("concurrent-migration", "wrong-secret", "wrong-migration-url"))
            if case == "pending-stopped":
                assert evidence["reconciledTask"] == "previous-task"
        print("OK", branch, component, case)


def main():
    parser = argparse.ArgumentParser(description="Exercita a publicação com respostas da AWS simuladas.")
    parser.add_argument("--api", required=True, type=Path)
    parser.add_argument("--ui", required=True, type=Path)
    parser.add_argument("--liquibase", required=True, type=Path)
    roots = vars(parser.parse_args())
    verify(roots, "api", "success", True)
    verify(roots, "api", "no-database", False, "update-service")
    verify(roots, "api", "wrong-generation", False, "update-service")
    verify(roots, "api", "missing-marker-provenance", False, "update-service")
    verify(roots, "api", "malformed-marker-provenance", False, "update-service")
    verify(roots, "api", "partial-retry", False)
    verify(roots, "api", "wrong-health", False, "update-service")
    verify(roots, "api", "different-digest", False)
    verify(roots, "api", "other-healthy-target", False)
    verify(roots, "api", "mutable", False, "update-service")
    verify(roots, "api", "stale", False, "update-service")
    verify(roots, "api", "build-failure", False, "update-service")
    verify(roots, "liquibase", "success", True)
    verify(roots, "liquibase", "migration-failure", False, "delete-parameter")
    verify(roots, "liquibase", "migration-timeout-running", False, "delete-parameter")
    verify(roots, "liquibase", "migration-timeout-stopped", True)
    verify(roots, "liquibase", "pending-running", False, "run-task")
    verify(roots, "liquibase", "pending-no-arn", False, "run-task")
    verify(roots, "liquibase", "pending-stopped", True)
    verify(roots, "liquibase", "concurrent-migration", False, "run-task")
    verify(roots, "liquibase", "migration-marker-write-failure", False, "delete-parameter")
    verify(roots, "ui", "success", True)
    verify(roots, "ui", "amplify-failure", False)
    verify(roots, "ui", "partial-retry", False)
    verify(roots, "ui", "tampered-archive", False, "create-deployment")
    verify(roots, "ui", "wrong-archive-revision", False, "create-deployment")
    verify(roots, "ui", "changed-archive-origin", False, "create-deployment")
    verify(roots, "ui", "changed-ui-archive-origin", False, "create-deployment")
    print("28 cenários de publicação simulada aprovados.")
    verify(roots, "api", "recover-success", True, "docker")
    verify(roots, "api", "recover-failed-source", False, "update-service")
    verify(roots, "api", "recover-wrong-evidence", False, "update-service")
    verify(roots, "api", "recover-wrong-generation", False, "update-service")
    verify(roots, "api", "recover-wrong-active-task", False, "update-service")
    verify(roots, "api", "recover-digest-mismatch", False, "update-service")
    verify(roots, "ui", "recover-success", True, "docker")
    verify(roots, "ui", "recover-tampered-archive", False, "create-deployment")
    verify(roots, "ui", "recover-wrong-amplify-job", False, "create-deployment")
    verify(roots, "liquibase", "recover-preview", True, "put-parameter")
    verify(roots, "liquibase", "recover-rollback", True, "put-parameter")
    verify(roots, "liquibase", "recover-shared-database", False, "run-task")
    verify(roots, "liquibase", "recover-wrong-task-url", False, "run-task")
    print("13 cenários de recuperação simulada aprovados.")
    production_cases = (
        ("api", "success", True, None),
        ("api", "other-environment-marker", False, "update-service"),
        ("api", "underprovisioned", False, "update-service"),
        ("api", "zone-rebalancing-off", False, "update-service"),
        ("api", "same-zone", False, None),
        ("api", "unsafe-local-identity", False, "update-service"),
        ("api", "unsafe-command", False, "update-service"),
        ("api", "wrong-secret", False, "update-service"),
        ("api", "recover-success", True, "docker"),
        ("api", "recover-other-environment", False, "update-service"),
        ("api", "recover-other-target", False, "update-service"),
        ("api", "recover-legacy-path", False, "update-service"),
        ("liquibase", "success", True, None),
        ("liquibase", "wrong-secret", False, "run-task"),
        ("liquibase", "wrong-migration-url", False, "run-task"),
        ("liquibase", "pending-other-environment", False, "run-task"),
        ("ui", "success", True, None),
        ("ui", "wrong-amplify-branch", False, "create-deployment"),
        ("ui", "recover-success", True, "docker"),
        ("ui", "recover-other-target", False, "create-deployment"),
    )
    for component, case, success, forbidden in production_cases:
        verify(roots, component, case, success, forbidden, branch="main")
    verify(roots, "api", "no-deploy-branch", False, "aws", branch="dev")
    print(f"{len(production_cases)} cenários de produção simulada e bloqueio de publicação de dev aprovados.")


if __name__ == "__main__":
    main()

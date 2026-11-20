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
component = env["COMPONENT"]
digest = "sha256:" + "b" * 64
account = "123456789012"
base = "arn:aws:ecs:us-east-1:" + account + ":task-definition/"
definition = base + component + ":2"
calls_path = Path("calls.jsonl")
previous = calls_path.read_text().splitlines() if calls_path.exists() else []
with calls_path.open("a") as out:
    out.write(json.dumps([tool, *args[:2]]) + "\n")
def output(value):
    print(json.dumps(value) if not isinstance(value, str) else value)
def refuse():
    raise SystemExit(61)

if tool == "gh":
    output("a" * 40 if case != "stale" else "f" * 40)
elif tool == "docker":
    if args[0] == "login":
        assert sys.stdin.read() == "mock-token\n"
    if case == "build-failure" and args[0] == "build":
        refuse()
elif tool == "npm":
    if args == ["run", "build"]:
        Path("dist").mkdir()
        Path("dist/index.html").write_text("<html/>")
elif tool == "curl":
    if "--config" in args:
        assert "https://signed.example.test/private-test-url" in sys.stdin.read()
    else:
        output({"sha": "a" * 40, "version": "v1.0.0"})
elif tool == "aws":
    group, action = args[:2]
    key = (group, action)
    if key == ("ssm", "get-parameter"):
        if case == "no-database":
            refuse()
        output({"environment": "hlg", "result": "success", "version": "v1.3.7", "sha": "c" * 40,
                "generation": "f01-story-001-base" if case != "wrong-generation" else "unexpected"})
    elif key == ("ecs", "describe-services"):
        activated = json.dumps(["aws", "ecs", "update-service"]) in previous
        output({"services": [{"status": "ACTIVE", "deploymentController": {"type": "ECS"},
                "taskDefinition": definition if activated else base + component + ":1", "runningCount": int(activated),
                "desiredCount": int(activated), "deployments": [{}] if activated else [],
                "loadBalancers": [{"targetGroupArn": "target-arn", "containerName": "api", "containerPort": 8080}]}]})
    elif key == ("elbv2", "describe-target-groups"):
        output({"TargetGroups": [{"TargetType": "ip", "HealthCheckPath": "/health/ready" if case != "wrong-health" else "/health",
                 "HealthCheckPort": "traffic-port", "Matcher": {"HttpCode": "200"}}]})
    elif key == ("ecs", "describe-task-definition"):
        environment = [{"name": name, "value": value} for name, value in [
            ("ASPNETCORE_ENVIRONMENT", "Homologation"), ("Hosting__PublicUiOrigin", env["UI_ORIGIN"]),
            ("Database__RequireTls", "true")]] if component == "api" else [
            {"name": "LIQUIBASE_COMMAND_URL", "value": "jdbc:fixture"}]
        secret_name = "ConnectionStrings__ScrapBook" if component == "api" else "LIQUIBASE_COMMAND_PASSWORD"
        output({"taskDefinition": {"family": component, "status": "ACTIVE", "networkMode": "awsvpc",
                 "requiresCompatibilities": ["FARGATE"], "containerDefinitions": [{"name": component,
                 "essential": True, "environment": environment, "image": "base",
                 "logConfiguration": {"logDriver": "awslogs"},
                 "secrets": [{"name": secret_name, "valueFrom": "arn:aws:secretsmanager:us-east-1:fixture"}]}]}})
    elif key == ("ecr", "describe-repositories"):
        output("MUTABLE" if case == "mutable" else "IMMUTABLE")
    elif key == ("ecr", "get-login-password"):
        output("mock-token")
    elif key == ("ecr", "describe-images"):
        output(digest)
    elif key == ("ecs", "register-task-definition"):
        value = json.load(sys.stdin)
        assert value["containerDefinitions"][0]["image"].endswith("@" + digest)
        assert "status" not in value
        output(definition)
    elif key == ("ecs", "update-service"):
        assert args[args.index("--task-definition") + 1] == definition
        assert args[args.index("--desired-count") + 1] == "1"
    elif key == ("ecs", "list-tasks"):
        output({"taskArns": ["task-api"]})
    elif key == ("ecs", "run-task"):
        assert args[args.index("--task-definition") + 1] == definition
        output({"failures": [], "tasks": [{"taskArn": "task-migration"}]})
    elif key == ("ecs", "describe-tasks"):
        output({"failures": [], "tasks": [{"taskDefinitionArn": definition,
                 "lastStatus": "RUNNING" if component == "api" else "STOPPED",
                 "attachments": [{"details": [{"name": "privateIPv4Address", "value": "10.0.0.42"}]}],
                 "containers": [{"name": component, "imageDigest": digest if case != "different-digest" else "sha256:changed",
                 "exitCode": 1 if case == "migration-failure" else 0}]}]})
    elif key == ("elbv2", "describe-target-health"):
        output({"TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"},
                 "Target": {"Id": "10.0.0.77" if case == "other-healthy-target" else "10.0.0.42", "Port": 8080}}]})
    elif key == ("ssm", "put-parameter"):
        marker = json.loads(args[args.index("--value") + 1])
        assert marker["result"] == "success" and marker["version"] == "v1.0.0"
    elif key == ("amplify", "create-deployment"):
        output({"jobId": "42", "zipUploadUrl": "https://signed.example.test/private-test-url"})
    elif key == ("amplify", "get-job"):
        output("FAILED" if case == "amplify-failure" else "SUCCEED")
    elif key in (("ecs", "wait"), ("amplify", "start-deployment")):
        pass
    else:
        raise AssertionError(key)
'''

scripts = {"api": "publish_ecs.sh", "ui": "publish_amplify.sh", "liquibase": "publish_migration.sh"}

def verify(roots, component, case, success, forbidden=None):
    with TemporaryDirectory(prefix="scrapbook-aws-fixture-") as directory:
        work = Path(directory)
        (work / "bin").mkdir()
        for command in ("aws", "docker", "gh", "npm", "curl"):
            path = work / "bin" / command
            path.write_text("#!/usr/bin/env python3\n" + FIXTURE)
            path.chmod(0o700)
        shutil.copytree(roots[component] / ".github/scripts", work / ".github/scripts")
        if component == "liquibase":
            (work / "config").mkdir()
            shutil.copyfile(roots[component] / "config/catalog-generation.txt", work / "config/catalog-generation.txt")
        local_env = {key: value for key, value in os.environ.items()
                     if not key.startswith(("AWS_", "GH_", "GITHUB_", "VITE_"))}
        env = local_env | {
            "PATH": f"{work / 'bin'}:{os.environ['PATH']}",
            "AWS_ACCOUNT_ID": "123456789012", "ECR_REPOSITORY": f"scrapbook-{component}",
            "GITHUB_SHA": "a" * 40, "GITHUB_REPOSITORY": f"scrapbook/{component}", "GITHUB_REF_NAME": "hlg",
            "GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1", "RELEASE_VERSION": "v1.0.0",
            "GH_TOKEN": "local-fixture-token", "COMPONENT": component, "FIXTURE": case,
            "ECS_CLUSTER": "cluster", "ECS_SERVICE": "service", "ALB_TARGET_GROUP_ARN": "target-arn",
            "DATABASE_GENERATION": "f01-story-001-base", "MIGRATION_TASK_DEFINITION": "liquibase:1",
            "MIGRATION_SUBNET_IDS": '["subnet-aaa", "subnet-bbb"]', "MIGRATION_GROUP_ID": "sg-abc",
            "AMPLIFY_APP_ID": "abc42", "AMPLIFY_BRANCH": "hlg", "UI_ORIGIN": "https://ui.hlg.example.test",
            "VITE_API_BASE_URL": "https://api.hlg.example.test",
        }
        command = f'source .github/scripts/release_support.sh && start_evidence && bash .github/scripts/{scripts[component]}'
        result = subprocess.run(["bash", "-c", command], cwd=work, env=env, text=True, capture_output=True, timeout=30)
        assert (result.returncode == 0) == success, (component, case, result)
        assert "private-test-url" not in result.stdout + result.stderr
        calls = (work / "calls.jsonl").read_text()
        if forbidden:
            assert forbidden not in calls, (case, calls)
        if success:
            evidence = json.loads((work / "release-evidence.json").read_text())
            assert ("activeImage" if component == "api" else "generation" if component == "liquibase"
                    else "archiveSha256") in evidence
        print("OK", component, case)


def main():
    parser = argparse.ArgumentParser(description="Exercita a publicação com respostas da AWS simuladas.")
    parser.add_argument("--api", required=True, type=Path)
    parser.add_argument("--ui", required=True, type=Path)
    parser.add_argument("--liquibase", required=True, type=Path)
    roots = vars(parser.parse_args())
    verify(roots, "api", "success", True)
    verify(roots, "api", "no-database", False, "update-service")
    verify(roots, "api", "wrong-generation", False, "update-service")
    verify(roots, "api", "wrong-health", False, "update-service")
    verify(roots, "api", "different-digest", False)
    verify(roots, "api", "other-healthy-target", False)
    verify(roots, "api", "mutable", False, "update-service")
    verify(roots, "api", "stale", False, "update-service")
    verify(roots, "api", "build-failure", False, "update-service")
    verify(roots, "liquibase", "success", True)
    verify(roots, "liquibase", "migration-failure", False, "put-parameter")
    verify(roots, "ui", "success", True)
    verify(roots, "ui", "amplify-failure", False)
    print("13 cenários de publicação simulada aprovados.")


if __name__ == "__main__":
    main()

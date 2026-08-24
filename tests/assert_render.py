#!/usr/bin/env python3
"""Behavioral assertions for rendered Wanderer manifests."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import yaml


def load(path: str) -> list[dict]:
    docs = [doc for doc in yaml.safe_load_all(Path(path).read_text()) if doc]
    assert docs, f"no manifests rendered from {path}"
    for doc in docs:
        assert doc.get("apiVersion"), doc
        assert doc.get("kind"), doc
        assert doc.get("metadata", {}).get("name"), doc
    keys = [
        (doc["kind"], doc["metadata"]["namespace"], doc["metadata"]["name"])
        for doc in docs
    ]
    duplicates = [key for key, count in Counter(keys).items() if count > 1]
    assert not duplicates, f"duplicate resources: {duplicates}"
    return docs


def by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [doc for doc in docs if doc["kind"] == kind]


def one(docs: list[dict], kind: str, name: str) -> dict:
    matches = [doc for doc in docs if doc["kind"] == kind and doc["metadata"]["name"] == name]
    assert len(matches) == 1, f"expected one {kind}/{name}, got {len(matches)}"
    return matches[0]


def env_map(container: dict) -> dict:
    return {entry["name"]: entry for entry in container.get("env", [])}


def assert_common(docs: list[dict]) -> None:
    web = one(docs, "Deployment", "wanderer-web")
    database = one(docs, "StatefulSet", "wanderer-database")
    search = one(docs, "StatefulSet", "wanderer-search")
    assert database["spec"]["replicas"] == 1
    assert search["spec"]["replicas"] == 1
    assert database["spec"]["template"]["spec"]["initContainers"][0]["name"] == "wait-for-search"
    assert web["spec"]["template"]["spec"]["initContainers"][0]["name"] == "wait-for-dependencies"
    web_container = web["spec"]["template"]["spec"]["containers"][0]
    db_container = database["spec"]["template"]["spec"]["containers"][0]
    search_container = search["spec"]["template"]["spec"]["containers"][0]
    web_env = env_map(web_container)
    db_env = env_map(db_container)
    search_env = env_map(search_container)
    assert "MEILI_MASTER_KEY" not in web_env
    assert db_env["MEILI_MASTER_KEY"]["valueFrom"]["secretKeyRef"]["name"]
    assert db_env["POCKETBASE_ENCRYPTION_KEY"]["valueFrom"]["secretKeyRef"]["name"]
    assert search_env["MEILI_MASTER_KEY"]["valueFrom"]["secretKeyRef"]["name"]
    assert "tcpSocket" in web_container["livenessProbe"]
    for pvc in by_kind(docs, "PersistentVolumeClaim"):
        assert pvc["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--mode", choices=["minimal", "production", "network"], required=True)
    args = parser.parse_args()
    docs = load(args.manifest)
    assert_common(docs)
    ingresses = by_kind(docs, "Ingress")
    policies = by_kind(docs, "NetworkPolicy")
    if args.mode == "minimal":
        assert not ingresses
        assert not policies
        assert not by_kind(docs, "Secret") or len(by_kind(docs, "Secret")) == 1
    elif args.mode == "production":
        assert {doc["metadata"]["name"] for doc in ingresses} == {"wanderer", "wanderer-database"}
        assert not by_kind(docs, "Secret"), "production example must use existingSecret"
    else:
        assert not ingresses
        assert {doc["metadata"]["name"] for doc in policies} == {
            "wanderer-web",
            "wanderer-database",
            "wanderer-search",
        }
        web_policy = one(docs, "NetworkPolicy", "wanderer-web")
        assert web_policy["spec"]["ingress"]
        assert any(rule.get("to") for rule in web_policy["spec"]["egress"])
    print(f"{args.mode}: {len(docs)} manifests verified")


if __name__ == "__main__":
    main()

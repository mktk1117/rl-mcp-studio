"""Training a recipe again from the studio.

`rlmcp recipe build` writes a directory; `rlmcp train --recipe` launches it and
opens the record for the rerun as `recipe-<run>`. The studio lists what is on
disk, writes one for a run on request, and presses rl-mcp's button -- it does
not interpret a recipe itself, so these tests check the listing, the store the
record lands in, and the command that goes out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rlmcp.records import open_store

from rlmcp_studio.runners import base as runners_base
from rlmcp_studio.runners import local as local_runner
from rlmcp_studio.sources import recipes


def _write_recipe(root: Path, from_run: str, task: str = "Fake-Task-G1") -> Path:
  directory = root / f"recipe-{from_run}"
  directory.mkdir(parents=True)
  (directory / "recipe.json").write_text(json.dumps({
      "schema_version": 1, "from_run": from_run, "task": task,
      "headline": "the one that worked", "task_packages": ["fake.tasks"],
      "seed": 42, "num_envs": 2048, "iterations": 6906, "device": "cuda:0",
      "package": None, "config": "config.json", "curriculum": "curriculum.json",
      "policy": "policy/model_final_6906.pt", "expect": {"joint_vel_rms": "2.8"},
      "verdict": "validated"}))
  (directory / "config.json").write_text("{}")
  (directory / "curriculum.json").write_text(json.dumps({"stages": []}))
  return directory


def test_recipes_on_disk_are_listed_for_their_task(tmp_path):
  root = tmp_path / "records"
  _write_recipe(root, "010")
  _write_recipe(root, "011", task="Other-Task")
  broken = root / "recipe-012"
  broken.mkdir()
  (broken / "recipe.json").write_text("{not json")

  mine = recipes.find_recipes([root], "Fake-Task-G1")
  everything = recipes.find_recipes([root])

  # The other task's recipe is out; the broken one stays visible, with its error.
  assert {r["name"] for r in mine} == {"recipe-010", "recipe-012"}
  ten = next(r for r in mine if r["name"] == "recipe-010")
  assert ten["task_packages"] == ["fake.tasks"] and ten["iterations"] == 6906
  assert ten["expect"] == {"joint_vel_rms": "2.8"}
  assert {r["name"] for r in everything} == {"recipe-010", "recipe-011", "recipe-012"}
  assert "recipe.json" in next(r for r in everything if r["name"] == "recipe-012")["error"]


def test_the_build_view_can_ask_for_them(client, records_root):
  _write_recipe(records_root, "010")

  answer = client.get("/api/build/recipes", params={"task": "Fake-Task-G1"}).json()

  assert [r["from_run"] for r in answer["recipes"]] == ["010"]
  assert answer["recipes"][0]["records_root"] == str(records_root)


def test_launching_a_recipe_opens_recipe_named_record_and_hands_rlmcp_the_recipe(
    client, records_root, monkeypatch):
  """Nothing to type: the recipe is the plan. The record is `recipe-<run>` in
  the store the recipe sits beside, and the command is `rlmcp train --recipe`
  on that directory with that store as its records root."""
  pytest.importorskip("rlmcp.records.recipe")
  recipe = _write_recipe(records_root, "001")     # 001 exists in the fixture store
  submitted = []

  async def fake_submit(self, spec):
    submitted.append(spec)
    return runners_base.Job(id="local-1", spec=spec, runner="local",
                            state=runners_base.JobState.RUNNING)
  monkeypatch.setattr(local_runner.LocalRunner, "submit", fake_submit)

  answer = client.post("/api/build/launch", json={"task": "Fake-Task-G1",
                                                  "recipe": str(recipe)})

  assert answer.status_code == 200, answer.text
  payload = answer.json()
  assert payload["record"] == "recipe-001" and payload["from_run"] == "001"
  record = open_store(records_root).get_record("recipe-001")
  assert record.task == "Fake-Task-G1" and record.parent == "001"
  assert record.proposed_by == "recipe" and "2.8" in record.prediction
  spec = submitted[0]
  assert spec.task == "Fake-Task-G1" and spec.record_id == "recipe-001"
  assert spec.args["recipe"] == str(recipe.resolve())
  assert spec.args["records-root"] == str(records_root)
  assert "--recipe" in payload["command"] and "train" in payload["command"]
  # A second press does not reuse the id.
  again = client.post("/api/build/launch", json={"task": "Fake-Task-G1",
                                                 "recipe": str(recipe)}).json()
  assert again["record"] == "recipe-001-2"


def test_a_recipe_without_a_manifest_is_refused_by_name(client, records_root):
  empty = records_root / "recipe-nothing"
  empty.mkdir()
  answer = client.post("/api/build/launch", json={"task": "Fake-Task-G1",
                                                  "recipe": str(empty)})
  assert answer.status_code == 400
  assert "recipe.json" in answer.json()["error"]


def test_writing_a_recipe_for_a_run_puts_it_beside_the_records(client, records_root):
  """`rlmcp recipe build` through the task interpreter, into `<root>/recipe-<id>/`."""
  pytest.importorskip("rlmcp.records.recipe")

  answer = client.post("/api/build/recipe", json={"task": "Fake-Task-G1",
                                                  "record": "001"})

  assert answer.status_code == 200, answer.text
  payload = answer.json()
  assert payload["ok"] and payload["path"] == str(records_root / "recipe-001")
  assert (records_root / "recipe-001" / "recipe.json").exists()
  assert payload["recipe"]["from_run"] == "001"
  listed = client.get("/api/build/recipes", params={"task": "Fake-Task-G1"}).json()
  assert [r["from_run"] for r in listed["recipes"]] == ["001"]

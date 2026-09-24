"""Run or export the teacher-reviewed tutoring evaluation set."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from importlib.resources import files
from pathlib import Path

from lingo.policy import build_tutor_policy


def load_cases(path: Path | None = None) -> list[dict]:
    source = path or files("lingo").joinpath("data/evaluation_cases.json")
    cases = json.loads(source.read_text())
    if len(cases) != 15:
        raise ValueError("the evaluation set must contain 15 cases")
    return cases


def export_cases(cases: list[dict], path: Path, responses: dict[str, str] | None = None) -> None:
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "id",
                "background",
                "learner_text",
                "context",
                "must_correct",
                "leave_alone",
                "model_response",
                "teacher_result",
                "teacher_notes",
            ],
        )
        writer.writeheader()
        for case in cases:
            writer.writerow(
                {
                    **case,
                    "must_correct": "; ".join(case["must_correct"]),
                    "leave_alone": "; ".join(case["leave_alone"]),
                    "model_response": (responses or {}).get(case["id"], ""),
                    "teacher_result": "",
                    "teacher_notes": "",
                }
            )


async def run_cases(cases: list[dict], model: str) -> dict[str, str]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    responses = {}
    for case in cases:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": build_tutor_policy()},
                {
                    "role": "user",
                    "content": f"Context: {case['context']}\nLearner says: {case['learner_text']}",
                },
            ],
        )
        responses[case["id"]] = response.choices[0].message.content or ""
    return responses


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export or run LinGo tutoring evaluations")
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--output", type=Path, default=Path("teacher-review.csv"))
    parser.add_argument("--run", action="store_true", help="Call OpenAI before exporting")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-6-luna"))
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)
    responses = asyncio.run(run_cases(cases, args.model)) if args.run else None
    export_cases(cases, args.output, responses)

"""gdpval_grader — standalone LLM-judge grader for GDPval deliverables.

Quick start (grade one task by id against a directory of deliverables)::

    from gdpval_grader import load_gdpval_task, grade_workspace

    task = load_gdpval_task("0a1b2c3d")          # from HuggingFace openai/gdpval
    result = grade_workspace(task, "./my_deliverables", api_key="sk-...")
    print(result["reward"])                       # continuous score in [0, 1]

Grade many tasks efficiently by reusing one evaluator (caches client +
meta-prompts)::

    from gdpval_grader import LLMEvaluator, grade_workspace
    ev = LLMEvaluator(api_key="sk-...")
    for task, ws in jobs:
        print(grade_workspace(task, ws, evaluator=ev)["reward"])
"""

from .evaluator import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LLMEvaluator,
    evaluate_rubric,
    grade_workspace,
)
from .tasks import (
    load_gdpval_task,
    load_gdpval_tasks,
    load_task_from_json,
)

__all__ = [
    "grade_workspace",
    "evaluate_rubric",
    "LLMEvaluator",
    "load_gdpval_task",
    "load_gdpval_tasks",
    "load_task_from_json",
    "DEFAULT_MODEL",
    "DEFAULT_BASE_URL",
]

__version__ = "0.1.0"

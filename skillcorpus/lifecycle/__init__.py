# SPDX-License-Identifier: MIT
"""Skill lifecycle: decide, per deployment model, which skills a model has internalized and can retire.

The pipeline synthesizes a task with hidden rubrics from each skill (``synthesize``), runs the agent without and
with the skill (``run_arms``), scores each run (``judge``), marks the rubric items that test the skill's own content
(``capability_items``), computes the unaided and aided capability scores and the validity gate (``labels``), and
issues a level-rule verdict per skill and model (``retire``). ``audit`` compares the level rule with gain-based
retirement rules. See README.md in this folder and the paper "Beyond Gain: Measuring Skill Internalization for Agent
Skill Retirement".

The modules are imported directly (``from skillcorpus.lifecycle import labels, retire``) so that each one also runs
as ``python -m skillcorpus.lifecycle.<module>``.
"""

from __future__ import annotations

from pathlib import Path

from smartlib.crowd.behavior import Agent, BehaviorGoal, BehaviorSystem, BehaviorTrace
from smartlib.crowd.interaction import InteractionSystem
from smartlib.crowd.schema import load_behavior_schema
from smartlib.crowd.yamlio import load_yaml


def run_single_agent_interaction_goal(
    interaction_yaml: str | Path,
    *,
    interaction_type: str,
    agent_id: str = "agent_001",
    schema_path: str | Path | None = None,
) -> BehaviorTrace:
    """Prototype: Find Seat -> Walk -> Align -> Sit Down -> Sit Idle for one agent."""

    schema = load_behavior_schema(schema_path)
    data = load_yaml(interaction_yaml)
    interactions = InteractionSystem.from_yaml_data(schema, data)
    behavior = BehaviorSystem(interactions)
    agent = Agent(id=agent_id)
    goal = BehaviorGoal(name=f"find_{interaction_type}", interaction_type=interaction_type)
    return behavior.execute_single_agent_goal(agent, goal)

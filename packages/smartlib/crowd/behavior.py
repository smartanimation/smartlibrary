from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from smartlib.crowd.interaction import InteractionPoint, InteractionSystem, Vector3


@dataclass
class Agent:
    id: str
    position: Vector3 = (0.0, 0.0, 0.0)
    rotation: Vector3 = (0.0, 0.0, 0.0)
    state: str = "idle"
    blackboard: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BehaviorGoal:
    name: str
    interaction_type: str
    idle_animation: str = "sit_idle"
    sit_animation: str = "sit_down"


@dataclass
class BehaviorTrace:
    agent_id: str
    goal: str
    steps: list[str] = field(default_factory=list)
    interaction_point_id: str = ""


@dataclass(frozen=True)
class BehaviorRuntimeSettings:
    dt: float = 1.0 / 24.0
    walk_speed: float = 1.2
    align_speed: float = 1.2
    arrive_distance: float = 0.05
    sit_distance: float = 0.05
    sit_down_duration: float = 40.0 / 24.0


@dataclass(frozen=True)
class BehaviorUpdate:
    agent_id: str
    state: str
    current_step: str
    current_clip: str
    interaction_point_id: str = ""
    distance_to_target: float = 0.0
    occupied: bool = False


class BehaviorSystem:
    """Owns intent and state transitions. World lookup is delegated to InteractionSystem."""

    def __init__(self, interactions: InteractionSystem):
        self.interactions = interactions

    def execute_single_agent_goal(self, agent: Agent, goal: BehaviorGoal) -> BehaviorTrace:
        self.interactions.schema.require_option("interaction_types", goal.interaction_type)
        trace = BehaviorTrace(agent_id=agent.id, goal=goal.name)

        self._step(agent, trace, "Find Seat")
        point = self.interactions.find_available(goal.interaction_type)
        if point is None:
            agent.state = "no_available_interaction"
            trace.steps.append("No Seat Available")
            return trace
        self.interactions.reserve(point.id, agent.id)
        trace.interaction_point_id = point.id
        agent.blackboard["interaction_point_id"] = point.id

        self._walk(agent, trace, point)
        self._align(agent, trace, point)
        self._sit_down(agent, trace, point, goal)
        self._sit_idle(agent, trace, point, goal)
        return trace

    def update_agent_goal(
        self,
        agent: Agent,
        goal: BehaviorGoal,
        *,
        settings: BehaviorRuntimeSettings | None = None,
    ) -> BehaviorUpdate:
        """Advance one agent using conditions instead of a fixed frame plan."""

        settings = settings or BehaviorRuntimeSettings()
        self.interactions.schema.require_option("interaction_types", goal.interaction_type)

        if not agent.blackboard.get("interaction_point_id"):
            point = self.interactions.find_available(goal.interaction_type)
            if point is None:
                agent.state = "no_available_interaction"
                return BehaviorUpdate(agent.id, agent.state, "No Seat Available", "", distance_to_target=0.0)
            self.interactions.reserve(point.id, agent.id)
            agent.blackboard["interaction_point_id"] = point.id
            agent.blackboard["state_elapsed"] = 0.0
            agent.state = "walking_to_interaction"

        point = self.interactions.get(str(agent.blackboard["interaction_point_id"]))

        if agent.state == "walking_to_interaction":
            distance = self._move_towards(agent, point.approach_position, settings.walk_speed, settings.dt)
            if distance <= settings.arrive_distance:
                agent.position = point.approach_position
                agent.state = "aligning_to_interaction"
                agent.blackboard["state_elapsed"] = 0.0
                return BehaviorUpdate(agent.id, agent.state, "Align", "walk", point.id, _distance(agent.position, point.position))
            return BehaviorUpdate(agent.id, agent.state, "Walk", "walk", point.id, distance)

        if agent.state == "aligning_to_interaction":
            distance = self._move_towards(agent, point.position, settings.align_speed, settings.dt)
            agent.rotation = point.rotation
            if distance <= settings.sit_distance:
                agent.position = point.position
                agent.state = "sitting_down"
                agent.blackboard["state_elapsed"] = 0.0
                return BehaviorUpdate(agent.id, agent.state, "Sit Down", goal.sit_animation, point.id, 0.0)
            return BehaviorUpdate(agent.id, agent.state, "Align", "walk", point.id, distance)

        if agent.state == "sitting_down":
            elapsed = float(agent.blackboard.get("state_elapsed", 0.0)) + settings.dt
            agent.blackboard["state_elapsed"] = elapsed
            agent.position = point.position
            agent.rotation = point.rotation
            if elapsed >= settings.sit_down_duration:
                self.interactions.occupy(point.id, agent.id)
                agent.state = goal.idle_animation
                return BehaviorUpdate(agent.id, agent.state, "Sit Idle", goal.idle_animation, point.id, 0.0, True)
            return BehaviorUpdate(agent.id, agent.state, "Sit Down", goal.sit_animation, point.id, 0.0)

        if agent.state == goal.idle_animation:
            return BehaviorUpdate(agent.id, agent.state, "Sit Idle", goal.idle_animation, point.id, 0.0, True)

        return BehaviorUpdate(agent.id, agent.state, agent.blackboard.get("behavior_step", ""), "", point.id)

    def _walk(self, agent: Agent, trace: BehaviorTrace, point: InteractionPoint) -> None:
        self._step(agent, trace, "Walk")
        agent.position = point.approach_position

    def _align(self, agent: Agent, trace: BehaviorTrace, point: InteractionPoint) -> None:
        self._step(agent, trace, "Align")
        agent.position = point.position
        agent.rotation = point.rotation

    def _sit_down(self, agent: Agent, trace: BehaviorTrace, point: InteractionPoint, goal: BehaviorGoal) -> None:
        self._step(agent, trace, "Sit Down")
        agent.state = goal.sit_animation
        agent.position = point.position

    def _sit_idle(self, agent: Agent, trace: BehaviorTrace, point: InteractionPoint, goal: BehaviorGoal) -> None:
        self._step(agent, trace, "Sit Idle")
        self.interactions.occupy(point.id, agent.id)
        agent.state = goal.idle_animation

    def _step(self, agent: Agent, trace: BehaviorTrace, step: str) -> None:
        agent.blackboard["behavior_step"] = step
        trace.steps.append(step)

    def _move_towards(self, agent: Agent, target: Vector3, speed: float, dt: float) -> float:
        distance = _distance(agent.position, target)
        if distance <= 1e-8:
            return 0.0
        step = max(0.0, speed) * max(0.0, dt)
        if step >= distance:
            agent.position = target
            return 0.0
        ratio = step / distance
        agent.position = tuple(agent.position[index] + (target[index] - agent.position[index]) * ratio for index in range(3))
        return _distance(agent.position, target)


def _distance(a: Vector3, b: Vector3) -> float:
    return math.sqrt(sum((float(a[index]) - float(b[index])) ** 2 for index in range(3)))

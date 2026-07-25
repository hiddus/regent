"""P1-B: Goal decomposition -> Work item creation tests."""

from __future__ import annotations

import uuid

from regent.application.goal_interpreter import (
    GoalInterpreter,
    GoalInterpretation,
    SubGoal,
)


class TestCreateWorkItems:
    """Test GoalInterpreter.create_work_items() static method."""

    def test_single_subgoal_creates_one_work_item(self) -> None:
        """A single SubGoal produces one WorkModel creation command."""
        sub_goals = [
            SubGoal(
                id="root",
                label="Build the thing",
                depends_on=[],
                acceptance_criteria={"done": True},
            ),
        ]
        goal_id = uuid.uuid4()
        correlation_id = uuid.uuid4()
        items = GoalInterpreter.create_work_items(
            sub_goals, goal_id=goal_id, correlation_id=correlation_id,
        )
        assert len(items) == 1
        assert items[0]["sub_goal_id"] == "root"
        assert items[0]["goal_id"] == goal_id
        assert items[0]["correlation_id"] == correlation_id
        assert items[0]["status"] == "PLANNED"
        assert items[0]["acceptance_criteria"] == {"done": True}

    def test_multiple_subgoals_with_dependencies(self) -> None:
        """Multiple SubGoals with depends_on produce correct dependency mapping."""
        sub_goals = [
            SubGoal(id="design", label="Design API", depends_on=[]),
            SubGoal(id="implement", label="Implement API", depends_on=["design"]),
            SubGoal(id="test", label="Write tests", depends_on=["implement"]),
        ]
        goal_id = uuid.uuid4()
        correlation_id = uuid.uuid4()
        items = GoalInterpreter.create_work_items(
            sub_goals, goal_id=goal_id, correlation_id=correlation_id,
        )
        assert len(items) == 3

        # design has no dependencies
        assert items[0]["sub_goal_id"] == "design"
        assert items[0]["depends_on_work_ids"] == []
        assert items[0]["dependency_ids"] == []

        # implement depends on design
        assert items[1]["sub_goal_id"] == "implement"
        assert len(items[1]["depends_on_work_ids"]) == 1
        assert "__pending__:design" in items[1]["depends_on_work_ids"]
        assert items[1]["dependency_ids"] == ["design"]

        # test depends on implement
        assert items[2]["sub_goal_id"] == "test"
        assert len(items[2]["depends_on_work_ids"]) == 1
        assert "__pending__:implement" in items[2]["depends_on_work_ids"]

    def test_priority_increments_with_index(self) -> None:
        """Work items get incrementing priority based on SubGoal order."""
        sub_goals = [
            SubGoal(id="a", label="First", depends_on=[]),
            SubGoal(id="b", label="Second", depends_on=["a"]),
            SubGoal(id="c", label="Third", depends_on=["b"]),
        ]
        items = GoalInterpreter.create_work_items(
            sub_goals, goal_id=uuid.uuid4(), correlation_id=uuid.uuid4(),
        )
        assert items[0]["priority"] == 0
        assert items[1]["priority"] == 1
        assert items[2]["priority"] == 2

    def test_metadata_contains_subgoal_info(self) -> None:
        """Work item metadata includes sub-goal label and deps."""
        sub_goals = [
            SubGoal(id="sg1", label="Do stuff", depends_on=["sg0"]),
        ]
        items = GoalInterpreter.create_work_items(
            sub_goals, goal_id=uuid.uuid4(), correlation_id=uuid.uuid4(),
        )
        meta = items[0]["metadata_json"]
        assert meta["sub_goal_label"] == "Do stuff"
        assert meta["sub_goal_deps"] == ["sg0"]

    def test_unknown_dependency_slug_ignored(self) -> None:
        """depends_on referencing unknown sub-goal slug is silently ignored."""
        sub_goals = [
            SubGoal(id="only", label="Only one", depends_on=["nonexistent"]),
        ]
        items = GoalInterpreter.create_work_items(
            sub_goals, goal_id=uuid.uuid4(), correlation_id=uuid.uuid4(),
        )
        # dependency_ids still records the slug
        assert items[0]["dependency_ids"] == ["nonexistent"]
        # but depends_on_work_ids only includes resolved ones
        assert items[0]["depends_on_work_ids"] == []

    def test_diamond_dependency_graph(self) -> None:
        """Diamond dependency: D depends on B and C, both depend on A."""
        sub_goals = [
            SubGoal(id="A", label="A", depends_on=[]),
            SubGoal(id="B", label="B", depends_on=["A"]),
            SubGoal(id="C", label="C", depends_on=["A"]),
            SubGoal(id="D", label="D", depends_on=["B", "C"]),
        ]
        items = GoalInterpreter.create_work_items(
            sub_goals, goal_id=uuid.uuid4(), correlation_id=uuid.uuid4(),
        )
        assert len(items) == 4
        d_deps = items[3]["depends_on_work_ids"]
        assert "__pending__:B" in d_deps
        assert "__pending__:C" in d_deps
        assert len(d_deps) == 2


class TestDecomposeAndCreateWorkItems:
    """Integration: decompose() output feeds into create_work_items()."""

    def test_fallback_single_subgoal(self) -> None:
        """When decompose falls back to single sub-goal, one work item is created."""
        # Simulate the fallback path (no LLM)
        interpretation = GoalInterpretation(
            objective="Build a widget",
            success_criteria={"widget_count": 1},
        )
        sub_goals = [
            SubGoal(
                id="root",
                label=interpretation.objective or "root",
                depends_on=[],
                acceptance_criteria=dict(interpretation.success_criteria),
            ),
        ]
        items = GoalInterpreter.create_work_items(
            sub_goals, goal_id=uuid.uuid4(), correlation_id=uuid.uuid4(),
        )
        assert len(items) == 1
        assert items[0]["purpose"] == "sub-goal:root: Build a widget"
        assert items[0]["acceptance_criteria"] == {"widget_count": 1}

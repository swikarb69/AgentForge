"""Task dependency analysis and cycle detection engine."""

from backend.app.planning.impact import ImpactResult
from backend.app.planning.models import ChangeType, Dependency, PlanTask


class DependencyAnalyzer:
    """Analyzes execution dependencies and detects graph cycles between plan tasks."""

    def analyze_dependencies(
        self,
        tasks: list[PlanTask],
        impact: ImpactResult | None = None,
    ) -> list[Dependency]:
        """Establish deterministic prerequisite dependencies between tasks.

        Rules:
        1. Foundation tasks (file creation) must precede tasks modifying that file.
        2. Implementation modification tasks precede test verification tasks.
        3. Test tasks depend on the implementation tasks they verify.
        4. Tasks within the same file execute sequentially by order.

        Args:
            tasks: List of generated PlanTask items.
            impact: Optional structured impact analysis.

        Returns:
            List of explicit Dependency objects linking dependent tasks.
        """
        dependencies: list[Dependency] = []
        task_by_id = {t.task_id: t for t in tasks}

        creation_tasks = [t for t in tasks if t.change_type == ChangeType.CREATE]
        modify_tasks = [t for t in tasks if t.change_type == ChangeType.MODIFY]
        test_tasks = [
            t
            for t in tasks
            if t.change_type == ChangeType.TEST
            or any(self._is_test_path(f) for f in t.target_files)
        ]

        # 1. Tasks modifying a newly created file depend on the file creation task
        for ct in creation_tasks:
            for created_file in ct.target_files:
                for mt in modify_tasks:
                    if created_file in mt.target_files and mt.task_id != ct.task_id:
                        self._add_dependency(
                            dependencies,
                            source_id=mt.task_id,
                            target_id=ct.task_id,
                            reason=(
                                f"File {created_file} must be created "
                                "before modification"
                            ),
                            task_by_id=task_by_id,
                        )

        # 2. Test tasks depend on implementation tasks
        impl_tasks = [
            t
            for t in tasks
            if t.change_type in (ChangeType.CREATE, ChangeType.MODIFY)
            and not any(self._is_test_path(f) for f in t.target_files)
        ]

        for tt in test_tasks:
            matched_impl = False
            # Look for corresponding implementation file
            for tf in tt.target_files:
                tf_stem = (
                    tf.replace("tests/", "").replace("test_", "").replace(".py", "")
                )
                for it in impl_tasks:
                    if any(tf_stem in f for f in it.target_files):
                        self._add_dependency(
                            dependencies,
                            source_id=tt.task_id,
                            target_id=it.task_id,
                            reason=(
                                f"Test task {tt.task_id} verifies implementation in "
                                f"{', '.join(it.target_files)}"
                            ),
                            task_by_id=task_by_id,
                        )
                        matched_impl = True

            # If no specific match, test task depends on preceding impl tasks
            if not matched_impl and impl_tasks:
                for it in impl_tasks:
                    if it.order < tt.order:
                        self._add_dependency(
                            dependencies,
                            source_id=tt.task_id,
                            target_id=it.task_id,
                            reason="Implementation must complete before test execution",
                            task_by_id=task_by_id,
                        )

        # 3. Tasks modifying the same file depend on each other in order
        files_to_tasks: dict[str, list[PlanTask]] = {}
        for t in tasks:
            for f in t.target_files:
                files_to_tasks.setdefault(f, []).append(t)

        for filepath, t_list in files_to_tasks.items():
            sorted_t = sorted(t_list, key=lambda x: x.order)
            for i in range(len(sorted_t) - 1):
                t_first = sorted_t[i]
                t_second = sorted_t[i + 1]
                if t_first.task_id != t_second.task_id:
                    self._add_dependency(
                        dependencies,
                        source_id=t_second.task_id,
                        target_id=t_first.task_id,
                        reason=f"Sequential changes to shared file {filepath}",
                        task_by_id=task_by_id,
                    )

        # Sync dependencies back into PlanTask.dependencies
        for t in tasks:
            t.dependencies = sorted(
                dict.fromkeys(
                    d.target_task_id
                    for d in dependencies
                    if d.source_task_id == t.task_id
                )
            )

        return dependencies

    def find_cycle(self, dependencies: list[Dependency]) -> list[str] | None:
        """Detect if the dependency graph contains any cycles.

        Returns:
            List of task IDs representing a cycle path if found, or None.
        """
        adjacency: dict[str, list[str]] = {}
        for dep in dependencies:
            adjacency.setdefault(dep.source_task_id, []).append(dep.target_task_id)

        visited: dict[str, int] = {}  # 0: unvisited, 1: visiting, 2: visited
        parent_path: list[str] = []

        def dfs(node: str) -> list[str] | None:
            visited[node] = 1
            parent_path.append(node)

            for neighbor in adjacency.get(node, []):
                if visited.get(neighbor, 0) == 1:
                    # Cycle detected: extract cycle loop
                    cycle_start_idx = parent_path.index(neighbor)
                    return parent_path[cycle_start_idx:] + [neighbor]
                if visited.get(neighbor, 0) == 0:
                    result = dfs(neighbor)
                    if result is not None:
                        return result

            parent_path.pop()
            visited[node] = 2
            return None

        for task_id in list(adjacency.keys()):
            if visited.get(task_id, 0) == 0:
                cycle = dfs(task_id)
                if cycle is not None:
                    return cycle

        return None

    def has_cycle(self, dependencies: list[Dependency]) -> bool:
        """Return True if the dependency relations contain a cycle."""
        return self.find_cycle(dependencies) is not None

    def topological_sort(
        self, tasks: list[PlanTask], dependencies: list[Dependency]
    ) -> list[str]:
        """Return task IDs sorted in topological dependency order.

        Raises:
            ValueError: If a cyclic dependency is detected.
        """
        cycle = self.find_cycle(dependencies)
        if cycle:
            raise ValueError(f"Cyclic dependency detected: {' -> '.join(cycle)}")

        in_degree: dict[str, int] = {t.task_id: 0 for t in tasks}
        adjacency: dict[str, list[str]] = {t.task_id: [] for t in tasks}

        for dep in dependencies:
            if dep.target_task_id in adjacency and dep.source_task_id in in_degree:
                # Target task must be done before source task
                adjacency[dep.target_task_id].append(dep.source_task_id)
                in_degree[dep.source_task_id] += 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        queue.sort(key=lambda tid: next(t.order for t in tasks if t.task_id == tid))

        sorted_ids: list[str] = []
        while queue:
            curr = queue.pop(0)
            sorted_ids.append(curr)

            for nxt in adjacency.get(curr, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
            queue.sort(key=lambda tid: next(t.order for t in tasks if t.task_id == tid))

        if len(sorted_ids) != len(tasks):
            raise ValueError("Dependency graph could not be topologically sorted")

        return sorted_ids

    @staticmethod
    def _add_dependency(
        dependencies: list[Dependency],
        source_id: str,
        target_id: str,
        reason: str,
        task_by_id: dict[str, PlanTask],
    ) -> None:
        """Add a dependency if not self-referential or already present."""
        if source_id == target_id:
            return
        if not any(
            d.source_task_id == source_id and d.target_task_id == target_id
            for d in dependencies
        ):
            dependencies.append(
                Dependency(
                    source_task_id=source_id,
                    target_task_id=target_id,
                    reason=reason,
                )
            )

    @staticmethod
    def _is_test_path(path: str) -> bool:
        low = path.lower()
        return low.startswith("tests/") or "test_" in low or "_test.py" in low

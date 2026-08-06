
from collections import defaultdict, deque

from .constants import GATEWAY_TYPES
from .nodes import get_node_size


def normalize_flows(flows):
    normalized = []

    for flow in flows:
        normalized.append(
            {
                **flow,
                "source": (
                    flow.get("sourceRef")
                    or flow.get("source")
                ),
                "target": (
                    flow.get("targetRef")
                    or flow.get("target")
                ),
            }
        )

    return normalized


def calculate_levels(nodes, flows):
    node_ids = {
        node["id"]
        for node in nodes
        if node.get("id")
    }

    outgoing = defaultdict(list)

    for flow in flows:
        source = flow.get("source")
        target = flow.get("target")

        if source in node_ids and target in node_ids:
            outgoing[source].append(target)

    start_nodes = [
        node["id"]
        for node in nodes
        if node.get("type") == "startEvent"
    ]

    if not start_nodes and nodes:
        start_nodes = [nodes[0]["id"]]

    levels = {}
    queue = deque()

    for start_id in start_nodes:
        levels[start_id] = 0
        queue.append(start_id)

    while queue:
        current = queue.popleft()

        for target in outgoing.get(current, []):
            next_level = levels[current] + 1

            if target not in levels or next_level > levels[target]:
                levels[target] = next_level
                queue.append(target)

    max_level = max(levels.values(), default=0)

    for node in nodes:
        node_id = node["id"]

        if node_id not in levels:
            max_level += 1
            levels[node_id] = max_level

    return levels


def calculate_branch_rows(nodes, flows):
    node_by_id = {
        node["id"]: node
        for node in nodes
        if node.get("id")
    }

    outgoing = defaultdict(list)

    for flow in flows:
        source = flow.get("source")
        target = flow.get("target")

        if source and target:
            outgoing[source].append(target)

    start_nodes = [
        node["id"]
        for node in nodes
        if node.get("type") == "startEvent"
    ]

    start_id = (
        start_nodes[0]
        if start_nodes
        else nodes[0]["id"]
    )

    rows = {start_id: 0}
    queue = deque([start_id])

    while queue:
        current = queue.popleft()
        current_row = rows.get(current, 0)
        targets = outgoing.get(current, [])

        current_type = node_by_id.get(
            current,
            {},
        ).get("type")

        if (
            current_type in GATEWAY_TYPES
            and len(targets) > 1
        ):
            center = (len(targets) - 1) / 2

            offsets = [
                index - center
                for index in range(len(targets))
            ]

            if len(targets) == 2:
                offsets = [-1, 1]

            for target, offset in zip(targets, offsets):
                if target not in rows:
                    rows[target] = current_row + offset
                    queue.append(target)

        else:
            for target in targets:
                if target not in rows:
                    rows[target] = current_row
                    queue.append(target)

    for node in nodes:
        rows.setdefault(node["id"], 0)

    return rows


def calculate_positions(nodes, flows):
    levels = calculate_levels(nodes, flows)
    rows = calculate_branch_rows(nodes, flows)

    horizontal_gap = 230
    vertical_gap = 190
    base_x = 80
    base_y = 300

    positions = {}

    for node in nodes:
        node_id = node["id"]
        node_type = node.get("type")

        width, height = get_node_size(node_type)

        positions[node_id] = {
            "x": base_x + levels[node_id] * horizontal_gap,
            "y": base_y + rows[node_id] * vertical_gap,
            "width": width,
            "height": height,
        }

    return positions

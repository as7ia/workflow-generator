
import xml.etree.ElementTree as ET

from .constants import DI_NS
from .xml_helpers import tag


def expand_rectangle(position, margin=15):
    return {
        "left": position["x"] - margin,
        "right": position["x"] + position["width"] + margin,
        "top": position["y"] - margin,
        "bottom": position["y"] + position["height"] + margin,
    }


def segment_intersects_rectangle(start, end, rectangle):
    x1, y1 = start
    x2, y2 = end

    if abs(x1 - x2) < 0.001:
        top = min(y1, y2)
        bottom = max(y1, y2)

        return (
            rectangle["left"] < x1 < rectangle["right"]
            and bottom > rectangle["top"]
            and top < rectangle["bottom"]
        )

    if abs(y1 - y2) < 0.001:
        left = min(x1, x2)
        right = max(x1, x2)

        return (
            rectangle["top"] < y1 < rectangle["bottom"]
            and right > rectangle["left"]
            and left < rectangle["right"]
        )

    return True


def simplify_path(points):
    result = []

    for point in points:
        point = (
            round(float(point[0]), 2),
            round(float(point[1]), 2),
        )

        if not result or point != result[-1]:
            result.append(point)

    if len(result) <= 2:
        return result

    simplified = [result[0]]

    for index in range(1, len(result) - 1):
        previous = simplified[-1]
        current = result[index]
        following = result[index + 1]

        vertical = (
            previous[0] == current[0] == following[0]
        )

        horizontal = (
            previous[1] == current[1] == following[1]
        )

        if not vertical and not horizontal:
            simplified.append(current)

    simplified.append(result[-1])

    return simplified


def get_connection_points(position):
    x = position["x"]
    y = position["y"]
    width = position["width"]
    height = position["height"]

    return {
        "left": (x, y + height / 2),
        "right": (x + width, y + height / 2),
        "top": (x + width / 2, y),
        "bottom": (x + width / 2, y + height),
    }


def offset_point(point, side, distance):
    x, y = point

    offsets = {
        "left": (-distance, 0),
        "right": (distance, 0),
        "top": (0, -distance),
        "bottom": (0, distance),
    }

    dx, dy = offsets[side]

    return x + dx, y + dy


def calculate_path_score(points, obstacles):
    score = 0

    for index in range(len(points) - 1):
        start = points[index]
        end = points[index + 1]

        score += (
            abs(end[0] - start[0])
            + abs(end[1] - start[1])
        )

        for obstacle in obstacles:
            if segment_intersects_rectangle(
                start,
                end,
                obstacle,
            ):
                score += 100000

    score += max(0, len(points) - 2) * 80

    return score


def calculate_edge_waypoints(
    source_id,
    target_id,
    node_positions,
    clearance=30,
):
    source = node_positions[source_id]
    target = node_positions[target_id]

    source_anchor = get_connection_points(source)["right"]
    target_anchor = get_connection_points(target)["left"]

    source_exit = offset_point(
        source_anchor,
        "right",
        clearance,
    )

    target_entry = offset_point(
        target_anchor,
        "left",
        clearance,
    )

    obstacles = [
        expand_rectangle(position)
        for node_id, position in node_positions.items()
        if node_id not in {source_id, target_id}
    ]

    source_x, source_y = source_exit
    target_x, target_y = target_entry

    middle_x = (source_x + target_x) / 2
    middle_y = (source_y + target_y) / 2

    candidates = [
        [
            source_anchor,
            source_exit,
            (middle_x, source_y),
            (middle_x, target_y),
            target_entry,
            target_anchor,
        ],
        [
            source_anchor,
            source_exit,
            (source_x, middle_y),
            (target_x, middle_y),
            target_entry,
            target_anchor,
        ],
    ]

    if obstacles:
        top_lane = min(
            obstacle["top"]
            for obstacle in obstacles
        ) - clearance

        bottom_lane = max(
            obstacle["bottom"]
            for obstacle in obstacles
        ) + clearance

        candidates.extend(
            [
                [
                    source_anchor,
                    source_exit,
                    (source_x, top_lane),
                    (target_x, top_lane),
                    target_entry,
                    target_anchor,
                ],
                [
                    source_anchor,
                    source_exit,
                    (source_x, bottom_lane),
                    (target_x, bottom_lane),
                    target_entry,
                    target_anchor,
                ],
            ]
        )

    candidates = [
        simplify_path(candidate)
        for candidate in candidates
    ]

    return min(
        candidates,
        key=lambda candidate: calculate_path_score(
            candidate,
            obstacles,
        ),
    )


def create_edge_waypoints(edge, waypoints):
    for x, y in waypoints:
        ET.SubElement(
            edge,
            tag(DI_NS, "waypoint"),
            {
                "x": str(x),
                "y": str(y),
            },
        )

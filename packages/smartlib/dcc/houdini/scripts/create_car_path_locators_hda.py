"""Create the smart::car_path_locators::1.0 Houdini SOP HDA.

Run this inside Houdini's Python Shell, or with hython:

    from smartlib.dcc.houdini import smart_menu
    smart_menu.create_car_path_locators_hda()

The generated HDA takes one path curve input and outputs five animated points:
car_root, wheel_FL, wheel_FR, wheel_RL, wheel_RR.
"""

from __future__ import annotations

import os


HDA_NAME = "smart::car_path_locators::1.0"
HDA_LABEL = "Smart Car Path Locators"


DETAIL_WRANGLE_VEX = r'''
function float primitive_path_length(const int geoh; const int primnum)
{
    int nv = primvertexcount(geoh, primnum);
    if (nv < 2)
        return 0.0;

    int vtx = primvertex(geoh, primnum, 0);
    int pt = vertexpoint(geoh, vtx);
    vector prev = point(geoh, "P", pt);
    float total = 0.0;

    for (int i = 1; i < nv; i++)
    {
        vtx = primvertex(geoh, primnum, i);
        pt = vertexpoint(geoh, vtx);
        vector p = point(geoh, "P", pt);
        total += distance(prev, p);
        prev = p;
    }

    return total;
}

function float path_length(const int geoh; const int primnum)
{
    int prim_count = nprimitives(geoh);
    if (prim_count <= 0)
        return 0.0;

    if (primnum >= 0)
        return primitive_path_length(geoh, clamp(primnum, 0, prim_count - 1));

    float total = 0.0;
    for (int pr = 0; pr < prim_count; pr++)
        total += primitive_path_length(geoh, pr);

    return total;
}

function vector catmull_rom_pos(const vector p0; const vector p1; const vector p2; const vector p3; const float u)
{
    float u2 = u * u;
    float u3 = u2 * u;
    return 0.5 * (
        2.0 * p1
        + (-p0 + p2) * u
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * u2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * u3
    );
}

function vector catmull_rom_tangent(const vector p0; const vector p1; const vector p2; const vector p3; const float u)
{
    float u2 = u * u;
    return 0.5 * (
        (-p0 + p2)
        + 2.0 * (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * u
        + 3.0 * (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * u2
    );
}

function vector prim_vertex_position(const int geoh; const int primnum; const int index)
{
    int nv = primvertexcount(geoh, primnum);
    int safe_index = clamp(index, 0, max(nv - 1, 0));
    int vtx = primvertex(geoh, primnum, safe_index);
    int pt = vertexpoint(geoh, vtx);
    return point(geoh, "P", pt);
}

function void sample_path(
    const int geoh;
    const int primnum;
    const float target_dist;
    const float total_len;
    export vector pos;
    export vector tangent;
    export float curveu)
{
    int prim_count = nprimitives(geoh);
    if (prim_count <= 0 || total_len <= 1e-6)
    {
        pos = 0;
        tangent = {0, 0, 1};
        curveu = 0.0;
        return;
    }

    float d = target_dist;
    if (chi("../loop_path"))
    {
        d = d - floor(d / total_len) * total_len;
        if (d < 0.0)
            d += total_len;
    }
    else
    {
        d = clamp(d, 0.0, total_len);
    }
    float accum = 0.0;
    vector lastp = 0;
    vector prevp = 0;
    int found_last = 0;

    int start_prim = primnum >= 0 ? clamp(primnum, 0, prim_count - 1) : 0;
    int end_prim = primnum >= 0 ? start_prim + 1 : prim_count;

    for (int pr = start_prim; pr < end_prim; pr++)
    {
        int nv = primvertexcount(geoh, pr);
        if (nv < 2)
            continue;

        int vtx0 = primvertex(geoh, pr, 0);
        int pt0 = vertexpoint(geoh, vtx0);
        vector p0 = point(geoh, "P", pt0);

        for (int i = 1; i < nv; i++)
        {
            int vtx1 = primvertex(geoh, pr, i);
            int pt1 = vertexpoint(geoh, vtx1);
            vector p1 = point(geoh, "P", pt1);
            vector seg = p1 - p0;
            float seglen = length(seg);

            if (seglen > 1e-6 && accum + seglen >= d)
            {
                float u = clamp((d - accum) / seglen, 0.0, 1.0);
                if (chi("../smooth_path") && nv > 2)
                {
                    vector cp0 = prim_vertex_position(geoh, pr, i - 2);
                    vector cp1 = p0;
                    vector cp2 = p1;
                    vector cp3 = prim_vertex_position(geoh, pr, i + 1);
                    pos = catmull_rom_pos(cp0, cp1, cp2, cp3, u);
                    tangent = normalize(catmull_rom_tangent(cp0, cp1, cp2, cp3, u));
                    if (length(tangent) <= 1e-6)
                        tangent = normalize(seg);
                }
                else
                {
                    pos = lerp(p0, p1, u);
                    tangent = normalize(seg);
                }
                curveu = (accum + seglen * u) / total_len;
                return;
            }

            if (seglen > 1e-6)
            {
                prevp = p0;
                lastp = p1;
                found_last = 1;
            }

            accum += seglen;
            p0 = p1;
        }
    }

    if (!found_last)
    {
        pos = 0;
        tangent = {0, 0, 1};
        curveu = 0.0;
        return;
    }

    pos = lastp;
    tangent = normalize(lastp - prevp);
    if (length(tangent) <= 1e-6)
        tangent = {0, 0, 1};
    curveu = 1.0;
}

function void pose_at(
    const float rear_dist;
    const float drive_dir;
    const int geoh;
    const int primnum;
    const float total_len;
    const float wheelbase;
    const float root_from_rear;
    const float root_height;
    const vector world_up;
    export vector rearp;
    export vector rootp;
    export vector fwd;
    export vector right;
    export vector upv;
    export float steer)
{
    vector rear_tan;
    vector frontp;
    vector front_tan;
    float u;

    sample_path(geoh, primnum, rear_dist, total_len, rearp, rear_tan, u);
    sample_path(geoh, primnum, rear_dist + wheelbase * drive_dir, total_len, frontp, front_tan, u);
    front_tan *= drive_dir;

    float steer_smooth = max(chf("../steer_smoothing_distance"), 0.0);
    if (steer_smooth > 1e-6)
    {
        vector smooth_a;
        vector smooth_b;
        vector smooth_tan_a;
        vector smooth_tan_b;
        float smooth_u;
        float front_dist = rear_dist + wheelbase * drive_dir;
        sample_path(geoh, primnum, front_dist - steer_smooth, total_len, smooth_a, smooth_tan_a, smooth_u);
        sample_path(geoh, primnum, front_dist + steer_smooth, total_len, smooth_b, smooth_tan_b, smooth_u);
        vector smooth_tan = (smooth_b - smooth_a) * drive_dir;
        if (length(smooth_tan) > 1e-6)
            front_tan = normalize(smooth_tan);
    }

    fwd = frontp - rearp;
    if (length(fwd) <= 1e-6)
        fwd = rear_tan;
    fwd = normalize(fwd);

    right = cross(world_up, fwd);
    if (length(right) <= 1e-6)
        right = {1, 0, 0};
    right = normalize(right);
    upv = normalize(cross(fwd, right));

    rootp = rearp + fwd * root_from_rear + upv * root_height;

    vector fwd_flat = normalize(fwd - upv * dot(fwd, upv));
    vector tan_flat = normalize(front_tan - upv * dot(front_tan, upv));
    if (length(tan_flat) <= 1e-6)
        tan_flat = fwd_flat;

    steer = atan2(dot(cross(fwd_flat, tan_flat), upv), dot(fwd_flat, tan_flat));
}

function void wheel_positions_at(
    const float rear_dist;
    const float drive_dir;
    const int geoh;
    const int primnum;
    const float total_len;
    const float wheelbase;
    const float root_from_rear;
    const float root_height;
    const float half_track;
    const float wheel_center_height;
    const vector world_up;
    export vector rootp;
    export vector fl;
    export vector fr;
    export vector rl;
    export vector rr;
    export vector fwd;
    export vector right;
    export vector upv;
    export float steer)
{
    vector rearp;
    pose_at(rear_dist, drive_dir, geoh, primnum, total_len, wheelbase, root_from_rear, root_height,
        world_up, rearp, rootp, fwd, right, upv, steer);

    vector rear_center = rearp + upv * wheel_center_height;
    vector front_center = rearp + fwd * wheelbase + upv * wheel_center_height;

    fl = front_center - right * half_track;
    fr = front_center + right * half_track;
    rl = rear_center - right * half_track;
    rr = rear_center + right * half_track;
}

function float spec_float_attr(const int geoh; const int pt; const string attr; const float fallback)
{
    if (pt >= 0 && haspointattrib(geoh, attr))
        return point(geoh, attr, pt);
    if (hasdetailattrib(geoh, attr))
        return detail(geoh, attr, 0);
    return fallback;
}

int primnum = chi("../path_primitive");
int prim_count = nprimitives(0);
if (prim_count <= 0)
{
    warning("Input 1 must be a path curve.");
    return;
}
if (primnum >= 0)
    primnum = clamp(primnum, 0, prim_count - 1);

float total_len = path_length(0, primnum);
if (total_len <= 1e-6)
{
    warning("Path curve is too short.");
    return;
}

float fps = max(chf("../fps"), 1e-3);
float start_frame = chf("../start_frame");
float start_distance = chf("../start_distance");
float speed = chf("../speed");
float wheelbase = max(chf("../wheelbase"), 1e-3);
float track_width = max(chf("../track_width"), 1e-3);
float wheel_radius = max(chf("../wheel_radius"), 1e-4);
float root_from_rear = chf("../root_from_rear");
float root_height = chf("../root_height");
float wheel_center_height = chf("../wheel_center_height");
float roll_step = max(chf("../roll_step_length"), 0.001);
int path_point = chi("../path_point");
int clamp_to_path = chi("../clamp_to_path");
int loop_path = chi("../loop_path");
vector world_up = normalize(chv("../world_up"));
if (length(world_up) <= 1e-6)
    world_up = {0, 1, 0};

int vehicle_source = chi("../vehicle_source");
int use_vehicle_spec = 0;
int spec_input_points = npoints(1);
int spec_root = -1;
int spec_fl = -1;
int spec_fr = -1;
int spec_rl = -1;
int spec_rr = -1;
if (vehicle_source == 1 && npoints(1) > 0)
{
    spec_root = findattribval(1, "point", "name", "car_root");
    spec_fl = findattribval(1, "point", "name", "wheel_FL");
    spec_fr = findattribval(1, "point", "name", "wheel_FR");
    spec_rl = findattribval(1, "point", "name", "wheel_RL");
    spec_rr = findattribval(1, "point", "name", "wheel_RR");

    if (spec_fl >= 0 && spec_fr >= 0 && spec_rl >= 0 && spec_rr >= 0)
    {
        vector spec_flp = point(1, "P", spec_fl);
        vector spec_frp = point(1, "P", spec_fr);
        vector spec_rlp = point(1, "P", spec_rl);
        vector spec_rrp = point(1, "P", spec_rr);
        vector spec_rear = (spec_rlp + spec_rrp) * 0.5;
        vector spec_front = (spec_flp + spec_frp) * 0.5;
        vector spec_fwd = normalize(spec_front - spec_rear);
        if (length(spec_fwd) <= 1e-6)
            spec_fwd = {0, 0, 1};

        wheelbase = max(distance(spec_front, spec_rear), 1e-3);
        track_width = max((distance(spec_flp, spec_frp) + distance(spec_rlp, spec_rrp)) * 0.5, 1e-3);
        wheel_radius = max(spec_float_attr(1, spec_fl, "wheel_radius", wheel_radius), 1e-4);
        wheel_center_height = spec_float_attr(1, spec_fl, "wheel_center_height", wheel_center_height);

        if (spec_root >= 0)
        {
            vector spec_rootp = point(1, "P", spec_root);
            vector root_delta = spec_rootp - spec_rear;
            root_from_rear = dot(root_delta, spec_fwd);
            root_height = dot(root_delta, world_up);
        }

        use_vehicle_spec = 1;
    }
    else
    {
        warning("Vehicle Source is Input 2 Vehicle Spec, but one or more required named points were not found.");
    }
}
else if (vehicle_source == 1)
{
    warning("Vehicle Source is Input 2 Vehicle Spec, but input 2 has no points.");
}

float seconds = (@Frame - start_frame) / fps;

for (int pr = nprimitives(0) - 1; pr >= 0; pr--)
    removeprim(0, pr, 1);
for (int pt = npoints(0) - 1; pt >= 0; pt--)
    removepoint(0, pt);

int vehicle_list_count = max(0, chi("../vehicles"));
int forward_count = max(1, chi("../traffic_count"));
int opposing_count = max(0, chi("../opposing_count"));
int total_instances = max(1, forward_count + opposing_count);
int placement_mode = chi("../traffic_placement_mode");
string placement_mode_token = chs("../traffic_placement_mode");
int use_random_range = placement_mode == 1
    || placement_mode_token == "random_range"
    || placement_mode_token == "Random Between Range"
    || placement_mode_token == "1"
    || chi("../traffic_use_random_range_toggle");
float traffic_spacing = chf("../traffic_spacing");
float distance_jitter = max(chf("../traffic_distance_jitter"), 0.0);
float random_range_start = chf("../traffic_random_start_distance");
float random_range_end = chf("../traffic_random_end_distance");
float lane_offset = chf("../traffic_lane_offset");
float lane_jitter = max(chf("../traffic_lane_jitter"), 0.0);
float opposing_lane_offset = chf("../opposing_lane_offset");
int traffic_seed = chi("../traffic_seed");
int unique_specs = chi("../traffic_unique_specs");

for (int instance = 0; instance < total_instances; instance++)
{
    int is_opposing = instance >= forward_count;
    int local_instance = is_opposing ? instance - forward_count : instance;
    float drive_dir = is_opposing ? -1.0 : 1.0;

    int spec_idx = -1;
    if (vehicle_list_count > 0)
    {
        if (unique_specs)
            spec_idx = instance % vehicle_list_count;
        else
            spec_idx = int(floor(rand(set(instance + traffic_seed * 13, traffic_seed, 0)) * vehicle_list_count));
        spec_idx = clamp(spec_idx, 0, vehicle_list_count - 1);
    }

    float inst_wheelbase = wheelbase;
    float inst_track_width = track_width;
    float inst_wheel_radius = wheel_radius;
    float inst_root_from_rear = root_from_rear;
    float inst_root_height = root_height;
    float inst_wheel_center_height = wheel_center_height;
    string inst_label = "Preview Car";
    string inst_namespace = "";
    string inst_spec_path = "";

    if (spec_idx >= 0)
    {
        int parm_idx = spec_idx + 1;
        inst_label = chs(sprintf("../vehicle%d_label", parm_idx));
        inst_namespace = chs(sprintf("../vehicle%d_namespace", parm_idx));
        inst_spec_path = chs(sprintf("../vehicle%d_spec_path", parm_idx));
        inst_wheelbase = max(chf(sprintf("../vehicle%d_wheelbase", parm_idx)), 1e-3);
        inst_track_width = max(chf(sprintf("../vehicle%d_track_width", parm_idx)), 1e-3);
        inst_wheel_radius = max(chf(sprintf("../vehicle%d_wheel_radius", parm_idx)), 1e-4);
        inst_root_from_rear = chf(sprintf("../vehicle%d_root_from_rear", parm_idx));
        inst_root_height = chf(sprintf("../vehicle%d_root_height", parm_idx));
        inst_wheel_center_height = chf(sprintf("../vehicle%d_wheel_center_height", parm_idx));
    }

    float dist_rand = (rand(set(instance + traffic_seed, 17, 3)) * 2.0 - 1.0) * distance_jitter;
    float lane_rand = (rand(set(instance + traffic_seed, 29, 7)) * 2.0 - 1.0) * lane_jitter;
    float inst_lane_offset = (is_opposing ? opposing_lane_offset : lane_offset) + lane_rand;
    float slot_spacing = traffic_spacing * float(local_instance);
    float max_rear = max(total_len - inst_wheelbase, 0.0);
    float inst_start = start_distance - drive_dir * slot_spacing + dist_rand;
    if (use_random_range)
    {
        float range_a = clamp(random_range_start, 0.0, max_rear);
        float range_b = random_range_end < 0.0 ? max_rear : clamp(random_range_end, 0.0, max_rear);
        if (range_b < range_a)
        {
            float swap_range = range_a;
            range_a = range_b;
            range_b = swap_range;
        }
        inst_start = lerp(range_a, range_b, rand(set(instance + traffic_seed * 31, 43, 11))) + dist_rand;
    }
    float path_dist = inst_start + speed * seconds * drive_dir;
    float rear_dist = path_dist;
    float rear_start = inst_start;

    if (path_point == 1)
    {
        rear_dist -= inst_root_from_rear * drive_dir;
        rear_start -= inst_root_from_rear * drive_dir;
    }

    if (clamp_to_path && !loop_path)
    {
        rear_dist = clamp(rear_dist, 0.0, max_rear);
        rear_start = clamp(rear_start, 0.0, max_rear);
    }

    float half_track = inst_track_width * 0.5;
    vector rootp, fl, fr, rl, rr;
    vector fwd, right, upv;
    float steer;
    wheel_positions_at(rear_dist, drive_dir, 0, primnum, total_len, inst_wheelbase, inst_root_from_rear, inst_root_height,
        half_track, inst_wheel_center_height, world_up, rootp, fl, fr, rl, rr, fwd, right, upv, steer);

    vector lane_shift = right * inst_lane_offset;
    rootp += lane_shift;
    fl += lane_shift;
    fr += lane_shift;
    rl += lane_shift;
    rr += lane_shift;

    float roll_fl = 0.0;
    float roll_fr = 0.0;
    float roll_rl = 0.0;
    float roll_rr = 0.0;
    float travel = rear_dist - rear_start;
    int steps = max(1, int(ceil(abs(travel) / roll_step)));
    float roll_sign = travel < 0.0 ? -1.0 : 1.0;

    vector prev_root, prev_fl, prev_fr, prev_rl, prev_rr;
    vector prev_fwd, prev_right, prev_up;
    float prev_steer;
    wheel_positions_at(rear_start, drive_dir, 0, primnum, total_len, inst_wheelbase, inst_root_from_rear, inst_root_height,
        half_track, inst_wheel_center_height, world_up, prev_root, prev_fl, prev_fr, prev_rl, prev_rr,
        prev_fwd, prev_right, prev_up, prev_steer);

    for (int step_i = 1; step_i <= steps; step_i++)
    {
        float u = float(step_i) / float(steps);
        float s = lerp(rear_start, rear_dist, u);
        vector step_root, step_fl, step_fr, step_rl, step_rr;
        vector step_fwd, step_right, step_up;
        float step_steer;
        wheel_positions_at(s, drive_dir, 0, primnum, total_len, inst_wheelbase, inst_root_from_rear, inst_root_height,
            half_track, inst_wheel_center_height, world_up, step_root, step_fl, step_fr, step_rl, step_rr,
            step_fwd, step_right, step_up, step_steer);

        roll_fl += distance(prev_fl, step_fl) * roll_sign / inst_wheel_radius;
        roll_fr += distance(prev_fr, step_fr) * roll_sign / inst_wheel_radius;
        roll_rl += distance(prev_rl, step_rl) * roll_sign / inst_wheel_radius;
        roll_rr += distance(prev_rr, step_rr) * roll_sign / inst_wheel_radius;

        prev_fl = step_fl;
        prev_fr = step_fr;
        prev_rl = step_rl;
        prev_rr = step_rr;
    }

    matrix3 root_m = maketransform(fwd, upv);
    vector4 root_q = quaternion(root_m);
    vector4 steer_q = quaternion(steer, upv);

    vector front_right = qrotate(steer_q, right);
    vector4 fl_q = qmultiply(quaternion(roll_fl, front_right), qmultiply(steer_q, root_q));
    vector4 fr_q = qmultiply(quaternion(roll_fr, front_right), qmultiply(steer_q, root_q));
    vector4 rl_q = qmultiply(quaternion(roll_rl, right), root_q);
    vector4 rr_q = qmultiply(quaternion(roll_rr, right), root_q);

    int root_pt = addpoint(0, rootp);
    int fl_pt = addpoint(0, fl);
    int fr_pt = addpoint(0, fr);
    int rl_pt = addpoint(0, rl);
    int rr_pt = addpoint(0, rr);

    string locator_names[] = array("car_root", "wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR");
    int pts[] = array(root_pt, fl_pt, fr_pt, rl_pt, rr_pt);
    vector4 orients[] = array(root_q, fl_q, fr_q, rl_q, rr_q);
    float rolls[] = array(0.0, roll_fl, roll_fr, roll_rl, roll_rr);
    float steers[] = array(0.0, steer, steer, 0.0, 0.0);
    string vehicle_id = sprintf("vehicle_%03d", instance + 1);

    for (int i = 0; i < len(pts); i++)
    {
        int pt = pts[i];
        string locator_name = locator_names[i];
        string point_name = total_instances == 1 ? locator_name : sprintf("%s/%s", vehicle_id, locator_name);
        string controller_parm = sprintf("../maya_%s_controller", locator_name);
        string roll_attr = "";
        string steer_attr = "";
        if (spec_idx >= 0)
        {
            int parm_idx = spec_idx + 1;
            controller_parm = sprintf("../vehicle%d_%s_controller", parm_idx, locator_name);
            roll_attr = chs(sprintf("../vehicle%d_%s_roll_attr", parm_idx, locator_name));
            steer_attr = chs(sprintf("../vehicle%d_%s_steer_attr", parm_idx, locator_name));
        }
        else
        {
            if (i > 0)
                roll_attr = chs(sprintf("../maya_%s_roll_attr", locator_name));
            if (i == 1 || i == 2)
                steer_attr = chs(sprintf("../maya_%s_steer_attr", locator_name));
        }

        setpointattrib(0, "name", pt, point_name, "set");
        setpointattrib(0, "locator_name", pt, locator_name, "set");
        setpointattrib(0, "vehicle_id", pt, vehicle_id, "set");
        setpointattrib(0, "vehicle_index", pt, instance, "set");
        setpointattrib(0, "vehicle_spec_index", pt, spec_idx, "set");
        setpointattrib(0, "vehicle_label", pt, inst_label, "set");
        setpointattrib(0, "vehicle_namespace", pt, inst_namespace, "set");
        setpointattrib(0, "vehicle_spec_path", pt, inst_spec_path, "set");
        setpointattrib(0, "maya_controller", pt, chs(controller_parm), "set");
        setpointattrib(0, "maya_roll_attr", pt, roll_attr, "set");
        setpointattrib(0, "maya_steer_attr", pt, steer_attr, "set");
        setpointattrib(0, "orient", pt, orients[i], "set");
        setpointattrib(0, "roll", pt, rolls[i], "set");
        setpointattrib(0, "roll_degrees", pt, degrees(rolls[i]), "set");
        setpointattrib(0, "steer", pt, steers[i], "set");
        setpointattrib(0, "steer_degrees", pt, degrees(steers[i]), "set");
        setpointattrib(0, "N", pt, fwd, "set");
        setpointattrib(0, "up", pt, upv, "set");
        setpointattrib(0, "pscale", pt, chf("../locator_size"), "set");
        setpointattrib(0, "vehicle_wheelbase", pt, inst_wheelbase, "set");
        setpointattrib(0, "vehicle_track_width", pt, inst_track_width, "set");
        setpointattrib(0, "vehicle_wheel_radius", pt, inst_wheel_radius, "set");
    }
}

setdetailattrib(0, "path_length", total_len, "set");
setdetailattrib(0, "speed", speed, "set");
setdetailattrib(0, "vehicle_count", total_instances, "set");
setdetailattrib(0, "vehicle_list_count", vehicle_list_count, "set");
setdetailattrib(0, "traffic_placement_mode", placement_mode, "set");
setdetailattrib(0, "traffic_placement_mode_token", placement_mode_token, "set");
setdetailattrib(0, "traffic_use_random_range", use_random_range, "set");
setdetailattrib(0, "traffic_random_start_distance", random_range_start, "set");
setdetailattrib(0, "traffic_random_end_distance", random_range_end, "set");
setdetailattrib(0, "vehicle_source_requested", vehicle_source, "set");
setdetailattrib(0, "vehicle_source_used", use_vehicle_spec, "set");
setdetailattrib(0, "vehicle_spec_input_points", spec_input_points, "set");
setdetailattrib(0, "vehicle_spec_found_car_root", spec_root >= 0, "set");
setdetailattrib(0, "vehicle_spec_found_wheel_FL", spec_fl >= 0, "set");
setdetailattrib(0, "vehicle_spec_found_wheel_FR", spec_fr >= 0, "set");
setdetailattrib(0, "vehicle_spec_found_wheel_RL", spec_rl >= 0, "set");
setdetailattrib(0, "vehicle_spec_found_wheel_RR", spec_rr >= 0, "set");
setdetailattrib(0, "wheelbase", wheelbase, "set");
setdetailattrib(0, "track_width", track_width, "set");
setdetailattrib(0, "wheel_radius", wheel_radius, "set");
setdetailattrib(0, "root_from_rear", root_from_rear, "set");
setdetailattrib(0, "root_height", root_height, "set");
setdetailattrib(0, "wheel_center_height", wheel_center_height, "set");
'''


PREVIEW_WRANGLE_VEX = r'''
function void add_preview_line(
    const int geoh;
    const vector a;
    const vector b;
    const string prim_name;
    const vector color)
{
    int p0 = addpoint(geoh, a);
    int p1 = addpoint(geoh, b);
    int pr = addprim(geoh, "polyline");
    addvertex(geoh, pr, p0);
    addvertex(geoh, pr, p1);
    setprimattrib(geoh, "name", pr, prim_name, "set");
    setprimattrib(geoh, "Cd", pr, color, "set");
}

function vector maya_rotation_axis_from_attr(
    const string raw;
    const vector xaxis;
    const vector yaxis;
    const vector zaxis;
    export int axis_id;
    export float sign)
{
    string clean = tolower(replace(replace(raw, "-", ""), "+", ""));
    sign = find(raw, "-") == 0 ? -1.0 : 1.0;

    if (find(clean, "rotatey") >= 0)
    {
        axis_id = 1;
        return normalize(yaxis);
    }
    if (find(clean, "rotatez") >= 0)
    {
        axis_id = 2;
        return normalize(zaxis);
    }

    axis_id = 0;
    return normalize(xaxis);
}

function void add_maya_roll_marker(
    const int geoh;
    const vector wp;
    const vector wheel_right;
    const vector wheel_up;
    const vector wheel_fwd;
    const float roll;
    const string roll_attr;
    const float wheel_size;
    const string prim_name;
    const vector wheel_color;
    const vector axis_color)
{
    int axis_id;
    float sign;
    vector axis = maya_rotation_axis_from_attr(roll_attr, wheel_right, wheel_up, wheel_fwd, axis_id, sign);
    if (length(axis) <= 1e-6)
        axis = normalize(wheel_right);

    vector a = wheel_up;
    vector b = wheel_fwd;
    if (axis_id == 1)
    {
        a = wheel_right;
        b = wheel_fwd;
    }
    else if (axis_id == 2)
    {
        a = wheel_right;
        b = wheel_up;
    }

    vector4 roll_q = quaternion(roll * sign, axis);
    a = normalize(qrotate(roll_q, a));
    b = normalize(qrotate(roll_q, b));

    add_preview_line(geoh, wp - a * wheel_size, wp + a * wheel_size, prim_name, wheel_color);
    add_preview_line(geoh, wp - b * wheel_size, wp + b * wheel_size, prim_name, wheel_color);
    add_preview_line(geoh, wp, wp + axis * wheel_size * 1.35, sprintf("%s_roll_axis", prim_name), axis_color);
}

function int find_vehicle_locator(const string vehicle_id; const string locator_name)
{
    for (int pt = 0; pt < npoints(0); pt++)
    {
        if (point(0, "vehicle_id", pt) == vehicle_id && point(0, "locator_name", pt) == locator_name)
            return pt;
    }
    return findattribval(0, "point", "name", locator_name);
}

if (chi("../output_mode") == 1 || !chi("../show_preview"))
    return;

vector body_color = chv("../preview_body_color");
vector forward_color = chv("../preview_forward_color");
vector steer_color = chv("../preview_steer_color");
vector wheel_color = chv("../preview_wheel_color");
float forward_len = chf("../preview_forward_length");
float steer_len = chf("../preview_steer_length");
float wheel_size = chf("../preview_wheel_marker_size");

for (int root = 0; root < npoints(0); root++)
{
    if (point(0, "locator_name", root) != "car_root" && point(0, "name", root) != "car_root")
        continue;

    string vehicle_id = point(0, "vehicle_id", root);
    int fl = find_vehicle_locator(vehicle_id, "wheel_FL");
    int fr = find_vehicle_locator(vehicle_id, "wheel_FR");
    int rl = find_vehicle_locator(vehicle_id, "wheel_RL");
    int rr = find_vehicle_locator(vehicle_id, "wheel_RR");

    if (fl >= 0 && fr >= 0 && rl >= 0 && rr >= 0)
    {
        int body = addprim(0, "polyline");
        addvertex(0, body, fl);
        addvertex(0, body, fr);
        addvertex(0, body, rr);
        addvertex(0, body, rl);
        addvertex(0, body, fl);
        setprimattrib(0, "name", body, sprintf("%s_preview_body", vehicle_id), "set");
        setprimattrib(0, "Cd", body, body_color, "set");
    }

    vector p = point(0, "P", root);
    vector fwd = normalize(point(0, "N", root));
    vector upv = normalize(point(0, "up", root));
    vector right = normalize(cross(upv, fwd));

    if (length(fwd) <= 1e-6)
        fwd = {0, 0, 1};
    if (length(upv) <= 1e-6)
        upv = {0, 1, 0};
    if (length(right) <= 1e-6)
        right = {1, 0, 0};

    add_preview_line(0, p, p + fwd * forward_len, sprintf("%s_preview_forward", vehicle_id), forward_color);

    int wheels[] = array(fl, fr, rl, rr);
    string wheel_names[] = array("preview_wheel_FL", "preview_wheel_FR", "preview_wheel_RL", "preview_wheel_RR");

    for (int i = 0; i < len(wheels); i++)
    {
        int pt = wheels[i];
        if (pt < 0)
            continue;

        vector wp = point(0, "P", pt);
        float wheel_steer = i < 2 ? point(0, "steer", pt) : 0.0;
        vector wheel_right = normalize(qrotate(quaternion(wheel_steer, upv), right));
        vector wheel_fwd = normalize(qrotate(quaternion(wheel_steer, upv), fwd));
        vector wheel_up = normalize(upv);
        if (length(wheel_right) <= 1e-6)
            wheel_right = right;
        if (length(wheel_fwd) <= 1e-6)
            wheel_fwd = fwd;
        if (length(wheel_up) <= 1e-6)
            wheel_up = upv;

        add_maya_roll_marker(
            0,
            wp,
            wheel_right,
            wheel_up,
            wheel_fwd,
            point(0, "roll", pt),
            point(0, "maya_roll_attr", pt),
            wheel_size,
            sprintf("%s_%s", vehicle_id, wheel_names[i]),
            wheel_color,
            steer_color
        );
    }

    if (fl >= 0)
    {
        vector wp = point(0, "P", fl);
        float steer = point(0, "steer", fl);
        vector steer_dir = normalize(qrotate(quaternion(steer, upv), fwd));
        add_preview_line(0, wp, wp + steer_dir * steer_len, sprintf("%s_preview_steer_FL", vehicle_id), steer_color);
    }

    if (fr >= 0)
    {
        vector wp = point(0, "P", fr);
        float steer = point(0, "steer", fr);
        vector steer_dir = normalize(qrotate(quaternion(steer, upv), fwd));
        add_preview_line(0, wp, wp + steer_dir * steer_len, sprintf("%s_preview_steer_FR", vehicle_id), steer_color);
    }
}
'''


def _set_if_exists(node, parm_name, value=None, expression=None):
    parm = node.parm(parm_name)
    if parm is None:
        return False
    if expression is not None:
        parm.setExpression(expression)
    elif value is not None:
        parm.set(value)
    return True


MAYA_ATTR_SYNC_CALLBACK = r'''
node = kwargs["node"]
parm_name = kwargs["parm"].name()

def _compose_attr(axis, direction):
    axis = str(axis or "off")
    direction = str(direction or "pos")
    if axis == "off":
        return ""
    attr = "rotate{}".format(axis.upper())
    if direction == "neg":
        attr = "-{}".format(attr)
    return attr

def _sync_attr(kind, wheel):
    axis_parm = node.parm("maya_{}_{}_axis".format(wheel, kind))
    direction_parm = node.parm("maya_{}_{}_direction".format(wheel, kind))
    attr_parm = node.parm("maya_{}_{}_attr".format(wheel, kind))
    if axis_parm is None or direction_parm is None or attr_parm is None:
        return
    attr_parm.set(_compose_attr(axis_parm.evalAsString(), direction_parm.evalAsString()))

for _wheel in ("wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR"):
    if parm_name in ("maya_{}_roll_axis".format(_wheel), "maya_{}_roll_direction".format(_wheel)):
        _sync_attr("roll", _wheel)
for _wheel in ("wheel_FL", "wheel_FR"):
    if parm_name in ("maya_{}_steer_axis".format(_wheel), "maya_{}_steer_direction".format(_wheel)):
        _sync_attr("steer", _wheel)
'''


MAYA_ATTR_PRESET_CALLBACK = r'''
node = kwargs["node"]
parm_name = kwargs["parm"].name()

def _set_if_exists(name, value):
    parm = node.parm(name)
    if parm is not None:
        parm.set(value)

def _compose_attr(axis, direction):
    if axis == "off":
        return ""
    attr = "rotate{}".format(axis.upper())
    if direction == "neg":
        attr = "-{}".format(attr)
    return attr

def _set_attr(kind, wheel, axis, direction):
    _set_if_exists("maya_{}_{}_axis".format(wheel, kind), axis)
    _set_if_exists("maya_{}_{}_direction".format(wheel, kind), direction)
    _set_if_exists("maya_{}_{}_attr".format(wheel, kind), _compose_attr(axis, direction))

if parm_name in ("maya_preset_roll_z_pos", "maya_preset_roll_z_neg"):
    direction = "neg" if parm_name.endswith("_neg") else "pos"
    for wheel in ("wheel_FL", "wheel_FR", "wheel_RL", "wheel_RR"):
        _set_attr("roll", wheel, "z", direction)
elif parm_name in ("maya_preset_front_steer_y_pos", "maya_preset_front_steer_y_neg"):
    direction = "neg" if parm_name.endswith("_neg") else "pos"
    for wheel in ("wheel_FL", "wheel_FR"):
        _set_attr("steer", wheel, "y", direction)
elif parm_name == "maya_preset_front_steer_off":
    for wheel in ("wheel_FL", "wheel_FR"):
        _set_attr("steer", wheel, "off", "pos")
'''


def _menu_with_callback(name, label, items, labels, default_value=0, join_with_next=False):
    import hou

    menu = hou.MenuParmTemplate(name, label, items, labels, default_value=default_value)
    menu.setScriptCallback(MAYA_ATTR_SYNC_CALLBACK)
    menu.setScriptCallbackLanguage(hou.scriptLanguage.Python)
    menu.setJoinWithNext(join_with_next)
    return menu


def _axis_menu(name, label, default_axis="off", join_with_next=True):
    items = ("off", "x", "y", "z")
    labels = ("Off", "X", "Y", "Z")
    default_value = items.index(default_axis)
    return _menu_with_callback(name, label, items, labels, default_value, join_with_next=join_with_next)


def _direction_menu(name, label, default_direction="pos", join_with_next=False):
    items = ("pos", "neg")
    labels = ("+", "-")
    default_value = items.index(default_direction)
    return _menu_with_callback(name, label, items, labels, default_value, join_with_next=join_with_next)


def _hidden_string(name, label, default_value=""):
    import hou

    parm = hou.StringParmTemplate(name, label, 1, default_value=(default_value,))
    parm.hide(True)
    return parm


def _hidden_float(name, label, default_value=0.0, min=0.0):
    import hou

    parm = hou.FloatParmTemplate(name, label, 1, default_value=(default_value,), min=min)
    parm.hide(True)
    return parm


def _preset_button(name, label, join_with_next=False):
    import hou

    button = hou.ButtonParmTemplate(name, label)
    button.setScriptCallback(MAYA_ATTR_PRESET_CALLBACK)
    button.setScriptCallbackLanguage(hou.scriptLanguage.Python)
    button.setJoinWithNext(join_with_next)
    return button


def _build_parm_template_group(node):
    import hou

    ptg = node.parmTemplateGroup()
    ptg.clear()

    motion = hou.FolderParmTemplate(
        "motion_folder",
        "Motion",
        (
            hou.FloatParmTemplate("speed", "Speed", 1, default_value=(4.0,), min=0.0),
            hou.FloatParmTemplate("start_frame", "Start Frame", 1, default_value=(1.0,)),
            hou.FloatParmTemplate("start_distance", "Start Distance", 1, default_value=(0.0,)),
            hou.FloatParmTemplate("fps", "FPS", 1, default_value=(24.0,), min=1.0),
            hou.MenuParmTemplate(
                "path_point",
                "Path Point",
                ("rear_axle", "car_root"),
                ("Rear Axle", "Car Root"),
                default_value=0,
            ),
            hou.ToggleParmTemplate("clamp_to_path", "Clamp To Path", default_value=True),
            hou.ToggleParmTemplate("loop_path", "Loop Path", default_value=False),
            hou.IntParmTemplate("path_primitive", "Path Primitive (-1 = All)", 1, default_value=(-1,), min=-1),
        ),
    )

    traffic = hou.FolderParmTemplate(
        "traffic_folder",
        "Traffic",
        (
            hou.IntParmTemplate("traffic_count", "Forward Count", 1, default_value=(1,), min=1),
            hou.MenuParmTemplate(
                "traffic_placement_mode",
                "Placement Mode",
                ("start_spacing", "random_range"),
                ("Start Distance + Spacing", "Random Between Range"),
                default_value=0,
            ),
            hou.ToggleParmTemplate("traffic_use_random_range_toggle", "Use Random Range", default_value=False),
            hou.FloatParmTemplate("traffic_spacing", "Spacing", 1, default_value=(6.0,), min=0.0),
            hou.FloatParmTemplate("traffic_distance_jitter", "Distance Jitter", 1, default_value=(0.0,), min=0.0),
            hou.FloatParmTemplate("traffic_random_start_distance", "Random Range Start", 1, default_value=(0.0,), min=0.0),
            hou.FloatParmTemplate("traffic_random_end_distance", "Random Range End (-1 = Path End)", 1, default_value=(-1.0,)),
            hou.FloatParmTemplate("traffic_lane_offset", "Lane Offset", 1, default_value=(0.0,)),
            hou.FloatParmTemplate("traffic_lane_jitter", "Lane Jitter", 1, default_value=(0.0,), min=0.0),
            hou.IntParmTemplate("opposing_count", "Opposing Count", 1, default_value=(0,), min=0),
            hou.FloatParmTemplate("opposing_lane_offset", "Opposing Lane Offset", 1, default_value=(0.0,)),
            hou.IntParmTemplate("traffic_seed", "Seed", 1, default_value=(1,)),
            hou.ToggleParmTemplate("traffic_unique_specs", "Use Each Vehicle Once", default_value=True),
        ),
    )

    vehicle = hou.FolderParmTemplate(
        "vehicle_folder",
        "Vehicle",
        (
            hou.MenuParmTemplate(
                "vehicle_source",
                "Vehicle Source",
                ("manual", "input2_vehicle_spec"),
                ("Manual Parameters", "Input 2 Vehicle Spec"),
                default_value=0,
            ),
            hou.FloatParmTemplate("wheelbase", "Wheelbase", 1, default_value=(2.8,), min=0.001),
            hou.FloatParmTemplate("track_width", "Track Width", 1, default_value=(1.6,), min=0.001),
            hou.FloatParmTemplate("wheel_radius", "Wheel Radius", 1, default_value=(0.34,), min=0.001),
            hou.FloatParmTemplate("root_from_rear", "Root From Rear Axle", 1, default_value=(1.4,)),
            hou.FloatParmTemplate("root_height", "Root Height", 1, default_value=(0.55,)),
            hou.FloatParmTemplate("wheel_center_height", "Wheel Center Height", 1, default_value=(0.34,)),
            hou.FloatParmTemplate("world_up", "World Up", 3, default_value=(0.0, 1.0, 0.0)),
        ),
    )

    vehicle_specs = hou.FolderParmTemplate(
        "vehicles",
        "Vehicles",
        (
            hou.StringParmTemplate("vehicle#_label", "Label", 1),
            hou.StringParmTemplate("vehicle#_namespace", "Maya Namespace", 1),
            hou.StringParmTemplate("vehicle#_spec_path", "Spec JSON", 1),
            _hidden_float("vehicle#_wheelbase", "Wheelbase", 2.8, min=0.001),
            _hidden_float("vehicle#_track_width", "Track Width", 1.6, min=0.001),
            _hidden_float("vehicle#_wheel_radius", "Wheel Radius", 0.34, min=0.001),
            _hidden_float("vehicle#_root_from_rear", "Root From Rear Axle", 1.4),
            _hidden_float("vehicle#_root_height", "Root Height", 0.55),
            _hidden_float("vehicle#_wheel_center_height", "Wheel Center Height", 0.34, min=0.001),
            _hidden_string("vehicle#_car_root_controller", "Car Root Controller"),
            _hidden_string("vehicle#_wheel_FL_controller", "Wheel FL Controller"),
            _hidden_string("vehicle#_wheel_FR_controller", "Wheel FR Controller"),
            _hidden_string("vehicle#_wheel_RL_controller", "Wheel RL Controller"),
            _hidden_string("vehicle#_wheel_RR_controller", "Wheel RR Controller"),
            _hidden_string("vehicle#_car_root_roll_attr", "Car Root Roll Attr"),
            _hidden_string("vehicle#_wheel_FL_roll_attr", "Wheel FL Roll Attr"),
            _hidden_string("vehicle#_wheel_FR_roll_attr", "Wheel FR Roll Attr"),
            _hidden_string("vehicle#_wheel_RL_roll_attr", "Wheel RL Roll Attr"),
            _hidden_string("vehicle#_wheel_RR_roll_attr", "Wheel RR Roll Attr"),
            _hidden_string("vehicle#_car_root_steer_attr", "Car Root Steer Attr"),
            _hidden_string("vehicle#_wheel_FL_steer_attr", "Wheel FL Steer Attr"),
            _hidden_string("vehicle#_wheel_FR_steer_attr", "Wheel FR Steer Attr"),
            _hidden_string("vehicle#_wheel_RL_steer_attr", "Wheel RL Steer Attr"),
            _hidden_string("vehicle#_wheel_RR_steer_attr", "Wheel RR Steer Attr"),
        ),
        folder_type=hou.folderType.MultiparmBlock,
    )

    maya_export_button = hou.ButtonParmTemplate("maya_export_animation_json", "Export Animation JSON")
    maya_export_button.setScriptCallback(
        'import importlib\nfrom smartlib.dcc.houdini import smart_menu\nimportlib.reload(smart_menu)\nsmart_menu.export_car_locator_anim_json(kwargs["node"])'
    )
    maya_export_button.setScriptCallbackLanguage(hou.scriptLanguage.Python)

    maya_import_spec_button = hou.ButtonParmTemplate("maya_import_vehicle_spec_json", "Add Vehicle Spec JSON")
    maya_import_spec_button.setScriptCallback(
        'import importlib\nfrom smartlib.dcc.houdini import smart_menu\nimportlib.reload(smart_menu)\nsmart_menu.import_vehicle_spec_json(kwargs["node"])'
    )
    maya_import_spec_button.setScriptCallbackLanguage(hou.scriptLanguage.Python)

    maya_export = hou.FolderParmTemplate(
        "maya_export_folder",
        "Maya Export",
        (
            maya_export_button,
            maya_import_spec_button,
            hou.ToggleParmTemplate("maya_create_validation_locators", "Create Validation Locators", default_value=True),
            hou.StringParmTemplate("maya_validation_locator_prefix", "Validation Locator Prefix", 1, default_value=("hda_",)),
            hou.FloatParmTemplate("maya_translate_scale", "Maya Translate Scale", 1, default_value=(100.0,), min=0.000001),
            hou.FloatParmTemplate("maya_wheel_roll_multiplier", "Wheel Roll Multiplier", 1, default_value=(0.00277778,)),
            hou.ToggleParmTemplate("maya_parent_validation_locators", "Parent Validation Locators", default_value=True),
            hou.ToggleParmTemplate("maya_key_controllers", "Key Controllers From JSON", default_value=False),
            hou.StringParmTemplate("maya_car_root_controller", "Car Root Controller", 1),
            hou.StringParmTemplate("maya_wheel_FL_controller", "Wheel FL Controller", 1),
            hou.StringParmTemplate("maya_wheel_FR_controller", "Wheel FR Controller", 1),
            hou.StringParmTemplate("maya_wheel_RL_controller", "Wheel RL Controller", 1),
            hou.StringParmTemplate("maya_wheel_RR_controller", "Wheel RR Controller", 1),
            hou.SeparatorParmTemplate("maya_wheel_presets_sep"),
            _preset_button("maya_preset_roll_z_pos", "All Wheel Roll Z +", join_with_next=True),
            _preset_button("maya_preset_roll_z_neg", "All Wheel Roll Z -"),
            _preset_button("maya_preset_front_steer_y_pos", "Front Steer Y +", join_with_next=True),
            _preset_button("maya_preset_front_steer_y_neg", "Front Steer Y -", join_with_next=True),
            _preset_button("maya_preset_front_steer_off", "Front Steer Off"),
            hou.SeparatorParmTemplate("maya_wheel_attr_sep"),
            _axis_menu("maya_wheel_FL_roll_axis", "Wheel FL Roll Axis", "z"),
            _direction_menu("maya_wheel_FL_roll_direction", "Wheel FL Roll Direction", "pos"),
            _axis_menu("maya_wheel_FR_roll_axis", "Wheel FR Roll Axis", "z"),
            _direction_menu("maya_wheel_FR_roll_direction", "Wheel FR Roll Direction", "pos"),
            _axis_menu("maya_wheel_RL_roll_axis", "Wheel RL Roll Axis", "z"),
            _direction_menu("maya_wheel_RL_roll_direction", "Wheel RL Roll Direction", "pos"),
            _axis_menu("maya_wheel_RR_roll_axis", "Wheel RR Roll Axis", "z"),
            _direction_menu("maya_wheel_RR_roll_direction", "Wheel RR Roll Direction", "pos"),
            _axis_menu("maya_wheel_FL_steer_axis", "Wheel FL Steer Axis", "off"),
            _direction_menu("maya_wheel_FL_steer_direction", "Wheel FL Steer Direction", "pos"),
            _axis_menu("maya_wheel_FR_steer_axis", "Wheel FR Steer Axis", "off"),
            _direction_menu("maya_wheel_FR_steer_direction", "Wheel FR Steer Direction", "pos"),
            _hidden_string("maya_wheel_FL_roll_attr", "Wheel FL Roll Attr", "rotateZ"),
            _hidden_string("maya_wheel_FR_roll_attr", "Wheel FR Roll Attr", "rotateZ"),
            _hidden_string("maya_wheel_RL_roll_attr", "Wheel RL Roll Attr", "rotateZ"),
            _hidden_string("maya_wheel_RR_roll_attr", "Wheel RR Roll Attr", "rotateZ"),
            _hidden_string("maya_wheel_FL_steer_attr", "Wheel FL Steer Attr", ""),
            _hidden_string("maya_wheel_FR_steer_attr", "Wheel FR Steer Attr", ""),
        ),
    )

    quality = hou.FolderParmTemplate(
        "quality_folder",
        "Quality",
        (
            hou.FloatParmTemplate("resample_length", "Path Resample Length", 1, default_value=(0.1,), min=0.001),
            hou.ToggleParmTemplate("smooth_path", "Smooth Path Interpolation", default_value=True),
            hou.FloatParmTemplate("steer_smoothing_distance", "Steer Smoothing Distance", 1, default_value=(0.5,), min=0.0),
            hou.FloatParmTemplate("roll_step_length", "Roll Integration Step", 1, default_value=(0.05,), min=0.001),
            hou.FloatParmTemplate("locator_size", "Locator Size", 1, default_value=(0.12,), min=0.001),
        ),
    )

    output = hou.FolderParmTemplate(
        "output_folder",
        "Output",
        (
            hou.MenuParmTemplate(
                "output_mode",
                "Output Mode",
                ("preview", "locators_only"),
                ("Preview", "Locators Only"),
                default_value=0,
            ),
        ),
    )

    preview = hou.FolderParmTemplate(
        "preview_folder",
        "Preview",
        (
            hou.ToggleParmTemplate("show_preview", "Show Preview Geometry", default_value=True),
            hou.FloatParmTemplate("preview_forward_length", "Forward Line Length", 1, default_value=(1.5,), min=0.001),
            hou.FloatParmTemplate("preview_steer_length", "Steer Line Length", 1, default_value=(0.7,), min=0.001),
            hou.FloatParmTemplate("preview_wheel_marker_size", "Wheel Marker Size", 1, default_value=(0.18,), min=0.001),
            hou.FloatParmTemplate("preview_body_color", "Body Color", 3, default_value=(0.1, 0.55, 1.0), min=0.0, max=1.0),
            hou.FloatParmTemplate("preview_forward_color", "Forward Color", 3, default_value=(0.1, 1.0, 0.35), min=0.0, max=1.0),
            hou.FloatParmTemplate("preview_steer_color", "Steer Color", 3, default_value=(1.0, 0.75, 0.1), min=0.0, max=1.0),
            hou.FloatParmTemplate("preview_wheel_color", "Wheel Color", 3, default_value=(1.0, 1.0, 1.0), min=0.0, max=1.0),
        ),
    )

    ptg.append(motion)
    ptg.append(traffic)
    ptg.append(vehicle)
    ptg.append(vehicle_specs)
    ptg.append(maya_export)
    ptg.append(quality)
    ptg.append(output)
    ptg.append(preview)
    return ptg


def _apply_parm_template_group(node):
    ptg = _build_parm_template_group(node)
    node.setParmTemplateGroup(ptg)
    _set_runtime_defaults(node)
    return ptg


def _set_runtime_defaults(node):
    fps = node.parm("fps")
    if fps is not None:
        fps.setExpression("$FPS")


def _build_network(subnet):
    import hou

    for child in subnet.children():
        child.destroy()

    convert = subnet.createNode("convertline", "path_to_polyline")
    convert.setInput(0, subnet.indirectInputs()[0])

    resample = subnet.createNode("resample", "resample_path")
    resample.setInput(0, convert)
    _set_if_exists(resample, "length", expression='ch("../resample_length")')

    wrangle = subnet.createNode("attribwrangle", "build_locators")
    wrangle.setInput(0, resample)
    wrangle.setInput(1, subnet.indirectInputs()[1])
    _set_if_exists(wrangle, "class", "detail")
    _set_if_exists(wrangle, "snippet", DETAIL_WRANGLE_VEX)

    preview = subnet.createNode("attribwrangle", "preview_geometry")
    preview.setInput(0, wrangle)
    _set_if_exists(preview, "class", "detail")
    _set_if_exists(preview, "snippet", PREVIEW_WRANGLE_VEX)

    out = subnet.createNode("null", "OUT_car_path_locators")
    out.setInput(0, preview)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)

    subnet.layoutChildren()


def create_hda():
    import hou

    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    hda_dir = os.path.join(workspace, "otls")
    os.makedirs(hda_dir, exist_ok=True)
    hda_path = os.path.join(hda_dir, "smart_car_path_locators.hda")

    obj = hou.node("/obj")
    build_geo = obj.createNode("geo", "build_smart_car_path_locators")
    for child in build_geo.children():
        child.destroy()

    subnet = build_geo.createNode("subnet", "smart_car_path_locators")
    _apply_parm_template_group(subnet)
    _build_network(subnet)

    hda_node = subnet.createDigitalAsset(
        name=HDA_NAME,
        hda_file_name=hda_path,
        description=HDA_LABEL,
        min_num_inputs=1,
        max_num_inputs=2,
    )

    ptg = _apply_parm_template_group(hda_node)
    hda_def = hda_node.type().definition()
    hda_def.setUserInfo("Input 1: path curve. Input 2: optional vehicle spec points. Output: car_root and four wheel locator points.")
    hda_def.updateFromNode(hda_node)
    hda_def.setParmTemplateGroup(ptg)
    hda_def.save(hda_path)
    hda_node.matchCurrentDefinition()
    _set_runtime_defaults(hda_node)

    build_geo.destroy()
    print("Created HDA:")
    print("  {}".format(hda_path.replace("\\", "/")))
    print("Operator:")
    print("  {}".format(HDA_NAME))
    print("Parameters:")
    print("  speed, start_frame, start_distance, fps, path_point, clamp_to_path, loop_path, path_primitive")
    print("  traffic_count, traffic_placement_mode, traffic_use_random_range_toggle, traffic_spacing, traffic_distance_jitter")
    print("  traffic_random_start_distance, traffic_random_end_distance, traffic_lane_offset, traffic_lane_jitter")
    print("  opposing_count, opposing_lane_offset, traffic_seed, traffic_unique_specs")
    print("  vehicle_source, wheelbase, track_width, wheel_radius, root_from_rear, root_height, wheel_center_height, world_up")
    print("  vehicles multiparm: vehicle#_label, vehicle#_namespace, vehicle#_spec_path")
    print("  maya_create_validation_locators, maya_validation_locator_prefix, maya_translate_scale")
    print("  maya_parent_validation_locators, maya_key_controllers")
    print("  maya_*_controller, maya_*_roll_attr, maya_*_steer_attr")
    print("  resample_length, smooth_path, steer_smoothing_distance, roll_step_length, locator_size")
    print("  output_mode")
    print("  show_preview, preview_forward_length, preview_steer_length, preview_wheel_marker_size")
    print("  preview_body_color, preview_forward_color, preview_steer_color, preview_wheel_color")


# Houdini's Python Source Editor often executes code as hou.session, not __main__.
# This file is a generation script, so run it immediately when executed.
create_hda()

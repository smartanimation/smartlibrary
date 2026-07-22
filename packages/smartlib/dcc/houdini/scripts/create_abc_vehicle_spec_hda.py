"""Create the smart::abc_vehicle_spec::1.0 Houdini SOP HDA.

Run inside Houdini:

    from smartlib.dcc.houdini import smart_menu
    smart_menu.create_abc_vehicle_spec_hda()

Use it after an Alembic SOP that reads Maya vehicle spec locators. The output is
five named points suitable for input 2 of smart::car_path_locators::1.0.
"""

from __future__ import annotations

import os


HDA_NAME = "smart::abc_vehicle_spec::1.0"
HDA_LABEL = "Smart ABC Vehicle Spec"


ABC_TO_SPEC_VEX = r'''
function string leaf_name(const string raw)
{
    string clean = replace(raw, "|", "/");
    string parts[] = split(clean, "/");
    string leaf = clean;
    if (len(parts) > 0)
        leaf = parts[len(parts) - 1];

    string namespaces[] = split(leaf, ":");
    if (len(namespaces) > 0)
        leaf = namespaces[len(namespaces) - 1];

    return leaf;
}

function string canonical_name(
    const string raw;
    const string token_root;
    const string token_fl;
    const string token_fr;
    const string token_rl;
    const string token_rr;
    const int use_shape_nodes)
{
    string n = tolower(leaf_name(raw));
    if (n == "")
        return "";

    if (!use_shape_nodes && match("*shape", n))
        return "";

    if (find(n, tolower(token_root)) >= 0)
        return "car_root";
    if (find(n, tolower(token_fl)) >= 0)
        return "wheel_FL";
    if (find(n, tolower(token_fr)) >= 0)
        return "wheel_FR";
    if (find(n, tolower(token_rl)) >= 0)
        return "wheel_RL";
    if (find(n, tolower(token_rr)) >= 0)
        return "wheel_RR";

    return "";
}

function string point_string_attrib(const int geoh; const int pt; const string attr)
{
    if (haspointattrib(geoh, attr))
        return point(geoh, attr, pt);
    return "";
}

function string prim_string_attrib(const int geoh; const int pr; const string attr)
{
    if (hasprimattrib(geoh, attr))
        return prim(geoh, attr, pr);
    return "";
}

function void store_candidate(
    const string cname;
    const vector pos;
    export int found_root;
    export int found_fl;
    export int found_fr;
    export int found_rl;
    export int found_rr;
    export vector root_p;
    export vector fl_p;
    export vector fr_p;
    export vector rl_p;
    export vector rr_p)
{
    if (cname == "car_root" && !found_root)
    {
        root_p = pos;
        found_root = 1;
    }
    else if (cname == "wheel_FL" && !found_fl)
    {
        fl_p = pos;
        found_fl = 1;
    }
    else if (cname == "wheel_FR" && !found_fr)
    {
        fr_p = pos;
        found_fr = 1;
    }
    else if (cname == "wheel_RL" && !found_rl)
    {
        rl_p = pos;
        found_rl = 1;
    }
    else if (cname == "wheel_RR" && !found_rr)
    {
        rr_p = pos;
        found_rr = 1;
    }
}

function void add_named_point(const int geoh; const string name; const vector pos; const float pscale)
{
    int pt = addpoint(geoh, pos);
    setpointattrib(geoh, "name", pt, name, "set");
    setpointattrib(geoh, "pscale", pt, pscale, "set");
}

function void add_line(const int geoh; const vector a; const vector b; const string name; const vector color)
{
    int p0 = addpoint(geoh, a);
    int p1 = addpoint(geoh, b);
    int pr = addprim(geoh, "polyline");
    addvertex(geoh, pr, p0);
    addvertex(geoh, pr, p1);
    setprimattrib(geoh, "name", pr, name, "set");
    setprimattrib(geoh, "Cd", pr, color, "set");
}

string token_root = chs("../token_car_root");
string token_fl = chs("../token_wheel_FL");
string token_fr = chs("../token_wheel_FR");
string token_rl = chs("../token_wheel_RL");
string token_rr = chs("../token_wheel_RR");
float scale = chf("../input_scale");
int use_shape_nodes = chi("../use_shape_nodes");

int found_root = 0;
int found_fl = 0;
int found_fr = 0;
int found_rl = 0;
int found_rr = 0;
vector root_p = 0;
vector fl_p = 0;
vector fr_p = 0;
vector rl_p = 0;
vector rr_p = 0;

for (int pt = 0; pt < npoints(0); pt++)
{
    string raw = point_string_attrib(0, pt, "name");
    if (raw == "")
        raw = point_string_attrib(0, pt, "path");
    if (raw == "")
        raw = point_string_attrib(0, pt, "abcobjectpath");

    string cname = canonical_name(raw, token_root, token_fl, token_fr, token_rl, token_rr, use_shape_nodes);
    if (cname != "")
        store_candidate(cname, point(0, "P", pt) * scale, found_root, found_fl, found_fr,
            found_rl, found_rr, root_p, fl_p, fr_p, rl_p, rr_p);
}

for (int pr = 0; pr < nprimitives(0); pr++)
{
    string raw = prim_string_attrib(0, pr, "name");
    if (raw == "")
        raw = prim_string_attrib(0, pr, "path");
    if (raw == "")
        raw = prim_string_attrib(0, pr, "abcobjectpath");

    string cname = canonical_name(raw, token_root, token_fl, token_fr, token_rl, token_rr, use_shape_nodes);
    if (cname == "")
        continue;

    int pts[] = primpoints(0, pr);
    if (len(pts) <= 0)
        continue;

    vector pos = point(0, "P", pts[0]) * scale;
    store_candidate(cname, pos, found_root, found_fl, found_fr, found_rl, found_rr,
        root_p, fl_p, fr_p, rl_p, rr_p);
}

for (int pr = nprimitives(0) - 1; pr >= 0; pr--)
    removeprim(0, pr, 1);
for (int pt = npoints(0) - 1; pt >= 0; pt--)
    removepoint(0, pt);

float pscale = chf("../point_size");
if (found_root)
    add_named_point(0, "car_root", root_p, pscale);
if (found_fl)
    add_named_point(0, "wheel_FL", fl_p, pscale);
if (found_fr)
    add_named_point(0, "wheel_FR", fr_p, pscale);
if (found_rl)
    add_named_point(0, "wheel_RL", rl_p, pscale);
if (found_rr)
    add_named_point(0, "wheel_RR", rr_p, pscale);

float wheel_radius = chf("../wheel_radius") * scale;
float wheel_center_height = chf("../wheel_center_height") * scale;
setdetailattrib(0, "wheel_radius", wheel_radius, "set");
setdetailattrib(0, "wheel_center_height", wheel_center_height, "set");
setdetailattrib(0, "found_car_root", found_root, "set");
setdetailattrib(0, "found_wheel_FL", found_fl, "set");
setdetailattrib(0, "found_wheel_FR", found_fr, "set");
setdetailattrib(0, "found_wheel_RL", found_rl, "set");
setdetailattrib(0, "found_wheel_RR", found_rr, "set");

if (chi("../show_preview") && found_fl && found_fr && found_rl && found_rr)
{
    vector body_color = chv("../preview_body_color");
    vector wheel_color = chv("../preview_wheel_color");

    int body = addprim(0, "polyline");
    int bfl = addpoint(0, fl_p);
    int bfr = addpoint(0, fr_p);
    int brr = addpoint(0, rr_p);
    int brl = addpoint(0, rl_p);
    addvertex(0, body, bfl);
    addvertex(0, body, bfr);
    addvertex(0, body, brr);
    addvertex(0, body, brl);
    addvertex(0, body, bfl);
    setprimattrib(0, "name", body, "preview_body", "set");
    setprimattrib(0, "Cd", body, body_color, "set");

    vector rear = (rl_p + rr_p) * 0.5;
    vector front = (fl_p + fr_p) * 0.5;
    vector fwd = normalize(front - rear);
    vector right = normalize(fr_p - fl_p);
    vector upv = normalize(cross(fwd, right));
    if (length(right) <= 1e-6)
        right = {1, 0, 0};
    if (length(upv) <= 1e-6)
        upv = {0, 1, 0};

    float marker = chf("../wheel_marker_size") * scale;
    vector wheel_positions[] = array(fl_p, fr_p, rl_p, rr_p);
    foreach (vector wp; wheel_positions)
    {
        add_line(0, wp - right * marker, wp + right * marker, "preview_wheel", wheel_color);
        add_line(0, wp - upv * marker, wp + upv * marker, "preview_wheel", wheel_color);
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


def _build_parm_template_group(node):
    import hou

    ptg = node.parmTemplateGroup()
    ptg.clear()

    source = hou.FolderParmTemplate(
        "source_folder",
        "Source",
        (
            hou.FloatParmTemplate("input_scale", "Input Scale", 1, default_value=(1.0,), min=0.000001),
            hou.StringParmTemplate("token_car_root", "Car Root Token", 1, default_value=("car_root",)),
            hou.StringParmTemplate("token_wheel_FL", "Wheel FL Token", 1, default_value=("wheel_FL",)),
            hou.StringParmTemplate("token_wheel_FR", "Wheel FR Token", 1, default_value=("wheel_FR",)),
            hou.StringParmTemplate("token_wheel_RL", "Wheel RL Token", 1, default_value=("wheel_RL",)),
            hou.StringParmTemplate("token_wheel_RR", "Wheel RR Token", 1, default_value=("wheel_RR",)),
            hou.ToggleParmTemplate("use_shape_nodes", "Use Shape Nodes", default_value=False),
        ),
    )

    vehicle = hou.FolderParmTemplate(
        "vehicle_folder",
        "Vehicle",
        (
            hou.FloatParmTemplate("wheel_radius", "Wheel Radius", 1, default_value=(0.34,), min=0.000001),
            hou.FloatParmTemplate("wheel_center_height", "Wheel Center Height", 1, default_value=(0.34,), min=0.0),
        ),
    )

    preview = hou.FolderParmTemplate(
        "preview_folder",
        "Preview",
        (
            hou.ToggleParmTemplate("show_preview", "Show Preview Geometry", default_value=True),
            hou.FloatParmTemplate("point_size", "Point Size", 1, default_value=(0.1,), min=0.000001),
            hou.FloatParmTemplate("wheel_marker_size", "Wheel Marker Size", 1, default_value=(0.18,), min=0.000001),
            hou.FloatParmTemplate("preview_body_color", "Body Color", 3, default_value=(0.1, 0.55, 1.0), min=0.0, max=1.0),
            hou.FloatParmTemplate("preview_wheel_color", "Wheel Color", 3, default_value=(1.0, 1.0, 1.0), min=0.0, max=1.0),
        ),
    )

    ptg.append(source)
    ptg.append(vehicle)
    ptg.append(preview)
    return ptg


def _apply_parm_template_group(node):
    ptg = _build_parm_template_group(node)
    node.setParmTemplateGroup(ptg)
    return ptg


def _build_network(subnet):
    import hou

    for child in subnet.children():
        child.destroy()

    wrangle = subnet.createNode("attribwrangle", "abc_to_vehicle_spec_points")
    wrangle.setInput(0, subnet.indirectInputs()[0])
    _set_if_exists(wrangle, "class", "detail")
    _set_if_exists(wrangle, "snippet", ABC_TO_SPEC_VEX)

    out = subnet.createNode("null", "OUT_vehicle_spec")
    out.setInput(0, wrangle)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)

    subnet.layoutChildren()


def create_hda():
    import hou

    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    hda_dir = os.path.join(workspace, "otls")
    os.makedirs(hda_dir, exist_ok=True)
    hda_path = os.path.join(hda_dir, "smart_abc_vehicle_spec.hda")

    obj = hou.node("/obj")
    build_geo = obj.createNode("geo", "build_smart_abc_vehicle_spec")
    for child in build_geo.children():
        child.destroy()

    subnet = build_geo.createNode("subnet", "smart_abc_vehicle_spec")
    _apply_parm_template_group(subnet)
    _build_network(subnet)

    hda_node = subnet.createDigitalAsset(
        name=HDA_NAME,
        hda_file_name=hda_path,
        description=HDA_LABEL,
        min_num_inputs=1,
        max_num_inputs=1,
    )

    ptg = _apply_parm_template_group(hda_node)
    hda_def = hda_node.type().definition()
    hda_def.setUserInfo("Input 1: Alembic locator geometry. Output: five vehicle spec points.")
    hda_def.updateFromNode(hda_node)
    hda_def.setParmTemplateGroup(ptg)
    hda_def.save(hda_path)
    hda_node.matchCurrentDefinition()

    build_geo.destroy()
    print("Created HDA:")
    print("  {}".format(hda_path.replace("\\", "/")))
    print("Operator:")
    print("  {}".format(HDA_NAME))


create_hda()

"""Create the smart::hair_groom::1.0 Houdini SOP HDA.

Run inside Houdini:

    from smartlib.dcc.houdini import smart_menu
    smart_menu.create_hair_groom_hda()

The generated HDA takes guide curves as input and outputs one of three views:
Karma render curves, low-resolution Maya proxy tubes, or ID debug curves.
"""

from __future__ import annotations

import os


HDA_NAME = "smart::hair_groom::1.0"
HDA_LABEL = "Smart Hair Groom"


RENDER_CURVES_VEX = r'''
function int source_guide_id(const int geoh; const int primnum)
{
    if (hasprimattrib(geoh, "guide_id"))
        return prim(geoh, "guide_id", primnum);
    if (hasprimattrib(geoh, "id"))
        return prim(geoh, "id", primnum);
    return primnum;
}

function string source_material(const int geoh; const int primnum; const string fallback)
{
    if (hasprimattrib(geoh, "material_name"))
    {
        string value = prim(geoh, "material_name", primnum);
        if (value != "")
            return value;
    }
    if (hasprimattrib(geoh, "shop_materialpath"))
    {
        string value = prim(geoh, "shop_materialpath", primnum);
        if (value != "")
            return value;
    }
    return fallback;
}

function vector stable_frame_side(const vector root; const vector tip)
{
    vector tangent = normalize(tip - root);
    if (length(tangent) <= 1e-6)
        tangent = {0, 1, 0};

    vector upv = chv("../guide_up");
    if (length(upv) <= 1e-6)
        upv = {0, 1, 0};
    upv = normalize(upv);

    vector side = normalize(cross(tangent, upv));
    if (length(side) <= 1e-6)
        side = {1, 0, 0};
    return side;
}

function void remove_source_geometry(const int source_prims; const int source_points)
{
    for (int pr = source_prims - 1; pr >= 0; pr--)
        removeprim(0, pr, 0);
    for (int pt = source_points - 1; pt >= 0; pt--)
        removepoint(0, pt);
}

function void add_sample_guides()
{
    int guide_count = 5;
    for (int g = 0; g < guide_count; g++)
    {
        float x = fit(g, 0, max(guide_count - 1, 1), -0.45, 0.45);
        int pr = addprim(0, "polyline");
        for (int i = 0; i < 7; i++)
        {
            float u = float(i) / 6.0;
            vector p = set(x + sin(u * 3.14159) * 0.08 * sign(x + 0.001),
                           u * 0.75,
                           -u * u * 0.35);
            int pt = addpoint(0, p);
            addvertex(0, pr, pt);
        }
        setprimattrib(0, "guide_id", pr, g, "set");
    }
}

int source_prims = nprimitives(0);
int source_points = npoints(0);
if (source_prims <= 0 && chi("../create_sample_guides"))
{
    add_sample_guides();
    source_prims = nprimitives(0);
    source_points = npoints(0);
}

int children = max(1, chi("../children_per_guide"));
float root_width = chf("../root_width");
float tip_width = chf("../tip_width");
float width_random = chf("../width_random");
float clump_radius = chf("../clump_radius");
float clump_taper = chf("../clump_taper");
float shape_oval = chf("../section_oval");
float noise_amp = chf("../strand_noise");
float noise_freq = chf("../strand_noise_frequency");
int clump_count = max(1, chi("../clump_count"));
int material_id = chi("../material_id");
string material_name = chs("../material_name");
int debug_colors = chi("../debug_colors");
int seed = chi("../random_seed");

for (int pr = 0; pr < source_prims; pr++)
{
    int nv = primvertexcount(0, pr);
    if (nv < 2)
        continue;

    int guide_id = source_guide_id(0, pr);
    int clump_id = abs(guide_id) % clump_count;
    string mat = source_material(0, pr, material_name);

    int first_vtx = primvertex(0, pr, 0);
    int last_vtx = primvertex(0, pr, nv - 1);
    vector root = point(0, "P", vertexpoint(0, first_vtx));
    vector tip = point(0, "P", vertexpoint(0, last_vtx));
    vector side = stable_frame_side(root, tip);
    vector tangent = normalize(tip - root);
    vector upv = normalize(cross(side, tangent));
    if (length(upv) <= 1e-6)
        upv = {0, 0, 1};

    for (int child = 0; child < children; child++)
    {
        float r0 = rand(set(guide_id * 17 + child, seed, 0));
        float r1 = rand(set(guide_id * 31 + child, seed, 1));
        float r2 = rand(set(guide_id * 47 + child, seed, 2));
        float angle = r0 * 6.28318530718;
        float radius = sqrt(r1) * clump_radius;
        vector child_side = side * cos(angle) + upv * sin(angle) * max(0.001, shape_oval);
        int strand_id = guide_id * 10000 + child;
        int hair_id = guide_id * children + child;

        int new_pr = addprim(0, "polyline");
        for (int i = 0; i < nv; i++)
        {
            int vtx = primvertex(0, pr, i);
            int src_pt = vertexpoint(0, vtx);
            vector p = point(0, "P", src_pt);
            float u = float(i) / float(max(nv - 1, 1));
            float keep_offset = pow(max(0.0, 1.0 - u), clump_taper);
            vector offset = child_side * radius * keep_offset;
            float noise = (rand(set(strand_id, i, seed)) - 0.5) * 2.0;
            offset += child_side * noise * noise_amp * sin(u * noise_freq * 6.28318530718);

            int new_pt = addpoint(0, p + offset);
            float width_rand = fit01(r2, 1.0 - width_random, 1.0 + width_random);
            float width = lerp(root_width, tip_width, u) * width_rand;
            setpointattrib(0, "width", new_pt, max(width, 0.000001), "set");
            setpointattrib(0, "pscale", new_pt, max(width * 0.5, 0.000001), "set");
            setpointattrib(0, "curveu", new_pt, u, "set");
            setpointattrib(0, "hair_id", new_pt, hair_id, "set");
            setpointattrib(0, "guide_id", new_pt, guide_id, "set");
            setpointattrib(0, "clump_id", new_pt, clump_id, "set");
            setpointattrib(0, "strand_id", new_pt, strand_id, "set");
            addvertex(0, new_pr, new_pt);
        }

        setprimattrib(0, "name", new_pr, sprintf("hair_%04d_%03d", guide_id, child), "set");
        setprimattrib(0, "hair_id", new_pr, hair_id, "set");
        setprimattrib(0, "guide_id", new_pr, guide_id, "set");
        setprimattrib(0, "clump_id", new_pr, clump_id, "set");
        setprimattrib(0, "strand_id", new_pr, strand_id, "set");
        setprimattrib(0, "material_id", new_pr, material_id, "set");
        setprimattrib(0, "material_name", new_pr, mat, "set");
        setprimattrib(0, "shop_materialpath", new_pr, mat, "set");
        if (debug_colors)
        {
            vector cd = hsvtorgb(set(frac(float(clump_id) / max(clump_count, 1) + 0.07 * child),
                                    0.65,
                                    1.0));
            setprimattrib(0, "Cd", new_pr, cd, "set");
        }
    }
}

remove_source_geometry(source_prims, source_points);
setdetailattrib(0, "smart_hair_output", "render_curves", "set");
setdetailattrib(0, "smart_hair_child_count", children, "set");
setdetailattrib(0, "smart_hair_version", "1.0", "set");
'''


PROXY_TUBES_VEX = r'''
function int source_guide_id(const int geoh; const int primnum)
{
    if (hasprimattrib(geoh, "guide_id"))
        return prim(geoh, "guide_id", primnum);
    if (hasprimattrib(geoh, "id"))
        return prim(geoh, "id", primnum);
    return primnum;
}

function void remove_source_geometry(const int source_prims; const int source_points)
{
    for (int pr = source_prims - 1; pr >= 0; pr--)
        removeprim(0, pr, 0);
    for (int pt = source_points - 1; pt >= 0; pt--)
        removepoint(0, pt);
}

function void add_sample_guides()
{
    int guide_count = 5;
    for (int g = 0; g < guide_count; g++)
    {
        float x = fit(g, 0, max(guide_count - 1, 1), -0.45, 0.45);
        int pr = addprim(0, "polyline");
        for (int i = 0; i < 7; i++)
        {
            float u = float(i) / 6.0;
            vector p = set(x + sin(u * 3.14159) * 0.08 * sign(x + 0.001),
                           u * 0.75,
                           -u * u * 0.35);
            int pt = addpoint(0, p);
            addvertex(0, pr, pt);
        }
        setprimattrib(0, "guide_id", pr, g, "set");
    }
}

int source_prims = nprimitives(0);
int source_points = npoints(0);
if (source_prims <= 0 && chi("../create_sample_guides"))
{
    add_sample_guides();
    source_prims = nprimitives(0);
    source_points = npoints(0);
}

int sides = max(3, chi("../proxy_sides"));
float root_width = chf("../root_width") * chf("../proxy_radius_scale");
float tip_width = chf("../tip_width") * chf("../proxy_radius_scale");
float clump_radius = chf("../clump_radius");
float section_oval = chf("../section_oval");
int clump_count = max(1, chi("../clump_count"));
int material_id = chi("../material_id");
string material_name = chs("../material_name");
vector up_hint = chv("../guide_up");
if (length(up_hint) <= 1e-6)
    up_hint = {0, 1, 0};
up_hint = normalize(up_hint);

for (int pr = 0; pr < source_prims; pr++)
{
    int nv = primvertexcount(0, pr);
    if (nv < 2)
        continue;

    int guide_id = source_guide_id(0, pr);
    int clump_id = abs(guide_id) % clump_count;
    int ring_points[];

    for (int i = 0; i < nv; i++)
    {
        int vtx = primvertex(0, pr, i);
        int pt = vertexpoint(0, vtx);
        vector p = point(0, "P", pt);

        vector prev = p;
        vector next = p;
        if (i > 0)
            prev = point(0, "P", vertexpoint(0, primvertex(0, pr, i - 1)));
        if (i < nv - 1)
            next = point(0, "P", vertexpoint(0, primvertex(0, pr, i + 1)));
        vector tangent = normalize(next - prev);
        if (length(tangent) <= 1e-6)
            tangent = {0, 1, 0};

        vector side = normalize(cross(tangent, up_hint));
        if (length(side) <= 1e-6)
            side = {1, 0, 0};
        vector upv = normalize(cross(side, tangent));
        if (length(upv) <= 1e-6)
            upv = {0, 0, 1};

        float u = float(i) / float(max(nv - 1, 1));
        float radius = max(lerp(root_width, tip_width, u) + clump_radius * 0.35 * pow(1.0 - u, 1.5), 0.000001);

        for (int s = 0; s < sides; s++)
        {
            float a = float(s) / float(sides) * 6.28318530718;
            vector q = p + side * cos(a) * radius + upv * sin(a) * radius * max(0.001, section_oval);
            int new_pt = addpoint(0, q);
            append(ring_points, new_pt);
            setpointattrib(0, "guide_id", new_pt, guide_id, "set");
            setpointattrib(0, "clump_id", new_pt, clump_id, "set");
            setpointattrib(0, "curveu", new_pt, u, "set");
        }
    }

    for (int i = 0; i < nv - 1; i++)
    {
        for (int s = 0; s < sides; s++)
        {
            int s_next = (s + 1) % sides;
            int p0 = ring_points[i * sides + s];
            int p1 = ring_points[i * sides + s_next];
            int p2 = ring_points[(i + 1) * sides + s_next];
            int p3 = ring_points[(i + 1) * sides + s];
            int quad = addprim(0, "poly");
            addvertex(0, quad, p0);
            addvertex(0, quad, p1);
            addvertex(0, quad, p2);
            addvertex(0, quad, p3);
            setprimattrib(0, "name", quad, sprintf("hairProxy_%04d", guide_id), "set");
            setprimattrib(0, "guide_id", quad, guide_id, "set");
            setprimattrib(0, "clump_id", quad, clump_id, "set");
            setprimattrib(0, "material_id", quad, material_id, "set");
            setprimattrib(0, "material_name", quad, material_name, "set");
            setprimattrib(0, "shop_materialpath", quad, material_name, "set");
        }
    }
}

remove_source_geometry(source_prims, source_points);
setdetailattrib(0, "smart_hair_output", "maya_proxy_tubes", "set");
setdetailattrib(0, "smart_hair_version", "1.0", "set");
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
            hou.MenuParmTemplate(
                "output_mode",
                "Output Mode",
                ("render", "proxy", "debug"),
                ("Render Curves", "Maya Proxy Tubes", "Debug IDs"),
                default_value=0,
            ),
            hou.ToggleParmTemplate("create_sample_guides", "Create Sample Guides When Empty", default_value=True),
            hou.FloatParmTemplate("guide_up", "Guide Up", 3, default_value=(0.0, 1.0, 0.0)),
            hou.IntParmTemplate("random_seed", "Random Seed", 1, default_value=(11,), min=0),
        ),
    )

    groom = hou.FolderParmTemplate(
        "groom_folder",
        "Groom",
        (
            hou.IntParmTemplate("children_per_guide", "Children Per Guide", 1, default_value=(8,), min=1),
            hou.IntParmTemplate("clump_count", "Clump Count", 1, default_value=(12,), min=1),
            hou.FloatParmTemplate("clump_radius", "Clump Radius", 1, default_value=(0.025,), min=0.0),
            hou.FloatParmTemplate("clump_taper", "Clump Taper", 1, default_value=(1.7,), min=0.0),
            hou.FloatParmTemplate("strand_noise", "Strand Noise", 1, default_value=(0.006,), min=0.0),
            hou.FloatParmTemplate("strand_noise_frequency", "Strand Noise Frequency", 1, default_value=(2.5,), min=0.0),
        ),
    )

    width = hou.FolderParmTemplate(
        "width_folder",
        "Width / Section",
        (
            hou.FloatParmTemplate("root_width", "Root Width", 1, default_value=(0.018,), min=0.000001),
            hou.FloatParmTemplate("tip_width", "Tip Width", 1, default_value=(0.002,), min=0.000001),
            hou.FloatParmTemplate("width_random", "Width Random", 1, default_value=(0.2,), min=0.0, max=1.0),
            hou.FloatParmTemplate("section_oval", "Section Oval", 1, default_value=(1.0,), min=0.001),
        ),
    )

    proxy = hou.FolderParmTemplate(
        "proxy_folder",
        "Maya Proxy",
        (
            hou.IntParmTemplate("proxy_sides", "Proxy Sides", 1, default_value=(6,), min=3),
            hou.FloatParmTemplate("proxy_radius_scale", "Proxy Radius Scale", 1, default_value=(2.0,), min=0.001),
        ),
    )

    look = hou.FolderParmTemplate(
        "look_folder",
        "Look Attributes",
        (
            hou.IntParmTemplate("material_id", "Material ID", 1, default_value=(0,), min=0),
            hou.StringParmTemplate("material_name", "Material Name", 1, default_value=("/materials/hair_default",)),
            hou.ToggleParmTemplate("debug_colors", "Debug Colors", default_value=True),
        ),
    )

    ptg.append(source)
    ptg.append(groom)
    ptg.append(width)
    ptg.append(proxy)
    ptg.append(look)
    return ptg


def _apply_parm_template_group(node):
    ptg = _build_parm_template_group(node)
    node.setParmTemplateGroup(ptg)
    return ptg


def _build_network(subnet):
    import hou

    for child in subnet.children():
        child.destroy()

    guide_input = subnet.indirectInputs()[0]

    render = subnet.createNode("attribwrangle", "build_render_curves")
    render.setInput(0, guide_input)
    _set_if_exists(render, "class", "detail")
    _set_if_exists(render, "snippet", RENDER_CURVES_VEX)

    proxy = subnet.createNode("attribwrangle", "build_maya_proxy_tubes")
    proxy.setInput(0, guide_input)
    _set_if_exists(proxy, "class", "detail")
    _set_if_exists(proxy, "snippet", PROXY_TUBES_VEX)

    debug = subnet.createNode("attribwrangle", "build_debug_id_curves")
    debug.setInput(0, guide_input)
    _set_if_exists(debug, "class", "detail")
    _set_if_exists(debug, "snippet", RENDER_CURVES_VEX.replace('int debug_colors = chi("../debug_colors");', "int debug_colors = 1;"))

    switch = subnet.createNode("switch", "switch_output_mode")
    switch.setInput(0, render)
    switch.setInput(1, proxy)
    switch.setInput(2, debug)
    _set_if_exists(switch, "input", expression='ch("../output_mode")')

    out = subnet.createNode("null", "OUT_hair_groom")
    out.setInput(0, switch)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)

    subnet.layoutChildren()


def create_hda():
    import hou

    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    hda_dir = os.path.join(workspace, "otls")
    os.makedirs(hda_dir, exist_ok=True)
    hda_path = os.path.join(hda_dir, "smart_hair_groom.hda")

    obj = hou.node("/obj")
    build_geo = obj.createNode("geo", "build_smart_hair_groom")
    for child in build_geo.children():
        child.destroy()

    subnet = build_geo.createNode("subnet", "smart_hair_groom")
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
    hda_def.setUserInfo(
        "Input 1: guide curves. Output: render curves, Maya proxy tubes, or debug ID curves."
    )
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

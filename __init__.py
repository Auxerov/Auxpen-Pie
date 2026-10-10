bl_info = {
    "name": "Auxpen Pie",
    "author": "Auxerov",
    "version": (1, 0, 0),
    "blender": (5, 0, 0),
    "location": "3D Viewport > Alt+Q (configurable)",
    "description": "Customizable pie menus for tablet use (default: Move / Rotate / Scale) with config import/export",
    "category": "3D View",
}

import ast
import json
import uuid

import bpy
import rna_keymap_ui
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Menu, Operator, PropertyGroup
from bpy_extras.io_utils import ExportHelper, ImportHelper

ADDON_ID = __name__
CONFIG_FORMAT = "grs_pie_config"
CONFIG_VERSION = 1
MAX_SLOTS = 8

SLOT_NAMES = (
    "Left",
    "Right",
    "Bottom",
    "Top",
    "Top-Left",
    "Top-Right",
    "Bottom-Left",
    "Bottom-Right",
)

KINDS = (
    ('OPERATOR', "Operator", "Run an operator"),
    ('MENU', "Menu", "Open a regular menu by its ID"),
    ('PIE', "Pie Menu", "Open another pie menu (a custom pie name or a pie menu ID)"),
    ('EMPTY', "Empty Slot", "Leave this slot empty"),
)
KIND_IDS = {kind[0] for kind in KINDS}

COMMAND_LABELS = {
    'OPERATOR': "Operator",
    'MENU': "Menu ID",
    'PIE': "Pie Name / ID",
}

DEFAULT_PIES = [
    {
        "name": "G R S",
        "shortcut": {"type": 'Q', "value": 'PRESS', "alt": True},
        "slots": [
            {"kind": 'OPERATOR', "label": "G  Move", "command": "transform.translate", "icon": 'EMPTY_ARROWS'},
            {"kind": 'OPERATOR', "label": "S  Scale", "command": "transform.resize", "icon": 'FULLSCREEN_ENTER'},
            {"kind": 'EMPTY'},
            {"kind": 'OPERATOR', "label": "R  Rotate", "command": "transform.rotate", "icon": 'FILE_REFRESH'},
        ],
    }
]

_menu_classes = []
_keymaps = []
_icon_cache = [None]
_attempts = [0]


def new_uid():
    return uuid.uuid4().hex[:8]


def menu_idname(uid):
    return "GRSPIE_MT_pie_" + uid


def get_prefs():
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def key_enum_ids(prop):
    return {item.identifier for item in bpy.types.KeyMapItem.bl_rna.properties[prop].enum_items}


def valid_icon(name):
    if _icon_cache[0] is None:
        try:
            _icon_cache[0] = set(
                bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys()
            )
        except Exception:
            _icon_cache[0] = set()
    if not _icon_cache[0]:
        return True
    return name in _icon_cache[0]


def operator_exists(idname):
    try:
        module, name = idname.split(".", 1)
        getattr(getattr(bpy.ops, module), name).get_rna_type()
        return True
    except Exception:
        return False


def parse_command(text):
    text = text.strip()
    if not text:
        return None
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError:
        return None

    kwargs = {}
    func = node
    if isinstance(node, ast.Call):
        if node.args:
            return None
        func = node.func
        try:
            for keyword in node.keywords:
                if keyword.arg is None:
                    return None
                kwargs[keyword.arg] = ast.literal_eval(keyword.value)
        except Exception:
            return None

    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return None
    parts.append(func.id)

    idname = ".".join(reversed(parts))
    if idname.startswith("bpy.ops."):
        idname = idname[len("bpy.ops."):]
    return idname, kwargs


def resolve_pie_idname(prefs, command):
    for pie in prefs.pies:
        if pie.name == command:
            return menu_idname(pie.uid)
    return command


def find_user_kmi(pie):
    kc = bpy.context.window_manager.keyconfigs.user
    if not kc:
        return None, None
    km = kc.keymaps.get("3D View")
    if not km:
        return None, None
    idname = menu_idname(pie.uid)
    for kmi in km.keymap_items:
        if kmi.idname == "wm.call_menu_pie" and kmi.properties and getattr(kmi.properties, "name", "") == idname:
            return km, kmi
    return km, None


def fill_pie(pie, data):
    pie.uid = new_uid()
    pie.name = str(data.get("name") or "Custom Pie")

    shortcut = data.get("shortcut")
    if not isinstance(shortcut, dict):
        shortcut = {}
    key = shortcut.get("type", 'NONE')
    value = shortcut.get("value", 'PRESS')
    pie.key_type = key if key in key_enum_ids("type") else 'NONE'
    pie.value = value if value in key_enum_ids("value") else 'PRESS'
    pie.ctrl = bool(shortcut.get("ctrl", False))
    pie.alt = bool(shortcut.get("alt", False))
    pie.shift = bool(shortcut.get("shift", False))
    pie.oskey = bool(shortcut.get("oskey", False))

    pie.slots.clear()
    slots = data.get("slots")
    if not isinstance(slots, list):
        slots = []
    for slot_data in slots[:MAX_SLOTS]:
        slot = pie.slots.add()
        if not isinstance(slot_data, dict):
            slot.kind = 'EMPTY'
            continue
        kind = slot_data.get("kind")
        slot.kind = kind if kind in KIND_IDS else 'EMPTY'
        slot.label = str(slot_data.get("label", ""))
        slot.command = str(slot_data.get("command", ""))
        icon = str(slot_data.get("icon", 'NONE'))
        slot.icon = icon if valid_icon(icon) else 'NONE'


def build_config(prefs):
    pies = []
    for pie in prefs.pies:
        km, kmi = find_user_kmi(pie)
        if kmi:
            shortcut = {
                "type": kmi.type,
                "value": kmi.value,
                "ctrl": kmi.ctrl,
                "alt": kmi.alt,
                "shift": kmi.shift,
                "oskey": kmi.oskey,
            }
        else:
            shortcut = {
                "type": pie.key_type,
                "value": pie.value,
                "ctrl": pie.ctrl,
                "alt": pie.alt,
                "shift": pie.shift,
                "oskey": pie.oskey,
            }
        pies.append({
            "name": pie.name,
            "shortcut": shortcut,
            "slots": [
                {"kind": slot.kind, "label": slot.label, "command": slot.command, "icon": slot.icon}
                for slot in pie.slots
            ],
        })
    return {"format": CONFIG_FORMAT, "version": CONFIG_VERSION, "pies": pies}


def draw_slot(layout, prefs, slot):
    icon = slot.icon if valid_icon(slot.icon) else 'NONE'
    command = slot.command.strip()

    if slot.kind == 'EMPTY':
        layout.separator()
        return

    if slot.kind == 'OPERATOR':
        parsed = parse_command(command)
        if parsed and operator_exists(parsed[0]):
            op = layout.operator(parsed[0], text=slot.label, icon=icon)
            for key, value in parsed[1].items():
                try:
                    setattr(op, key, value)
                except (AttributeError, TypeError, ValueError):
                    pass
            return
    elif slot.kind == 'MENU' and command:
        layout.menu(command, text=slot.label, icon=icon)
        return
    elif slot.kind == 'PIE' and command:
        op = layout.operator("wm.call_menu_pie", text=slot.label or command, icon=icon)
        op.name = resolve_pie_idname(prefs, command)
        return

    layout.label(text="Invalid: " + (slot.label or command or "?"), icon='ERROR')


def draw_pie_menu(pie_layout, uid):
    prefs = get_prefs()
    if not prefs:
        return
    pie = next((p for p in prefs.pies if p.uid == uid), None)
    if not pie:
        return
    for index, slot in enumerate(pie.slots):
        if index >= MAX_SLOTS:
            break
        draw_slot(pie_layout, prefs, slot)


def make_menu_class(uid, label):
    def draw(self, context):
        draw_pie_menu(self.layout.menu_pie(), uid)

    return type(
        menu_idname(uid),
        (Menu,),
        {
            "bl_idname": menu_idname(uid),
            "bl_label": label or "Pie",
            "draw": draw,
        },
    )


def clear_runtime():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()

    for cls in reversed(_menu_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    _menu_classes.clear()


def build_runtime(prefs):
    kc = bpy.context.window_manager.keyconfigs.addon
    km = kc.keymaps.new(name="3D View", space_type='VIEW_3D') if kc else None

    for pie in prefs.pies:
        cls = make_menu_class(pie.uid, pie.name)
        bpy.utils.register_class(cls)
        _menu_classes.append(cls)

        if km:
            kmi = km.keymap_items.new(
                "wm.call_menu_pie",
                pie.key_type,
                pie.value,
                ctrl=int(pie.ctrl),
                alt=int(pie.alt),
                shift=int(pie.shift),
                oskey=int(pie.oskey),
            )
            kmi.properties.name = menu_idname(pie.uid)
            _keymaps.append((km, kmi))


def rebuild():
    clear_runtime()
    prefs = get_prefs()
    if prefs:
        build_runtime(prefs)


def init_runtime():
    prefs = get_prefs()
    if prefs is None:
        return False
    if not prefs.initialized and len(prefs.pies) == 0:
        for pie_data in DEFAULT_PIES:
            fill_pie(prefs.pies.add(), pie_data)
    prefs.initialized = True
    clear_runtime()
    build_runtime(prefs)
    return True


def deferred_init():
    _attempts[0] += 1
    if init_runtime() or _attempts[0] > 50:
        return None
    return 0.2


class GRSPIE_Slot(PropertyGroup):
    kind: EnumProperty(name="Type", items=KINDS, default='OPERATOR')
    label: StringProperty(name="Label", description="Text shown in the pie slot")
    command: StringProperty(
        name="Command",
        description="Operator (e.g. transform.translate or object.mode_set(mode='EDIT')), a menu ID, or a pie name / ID",
    )
    icon: StringProperty(name="Icon", description="Blender icon identifier, e.g. EMPTY_ARROWS", default='NONE')


class GRSPIE_Pie(PropertyGroup):
    uid: StringProperty()
    name: StringProperty(name="Name", default="Custom Pie")
    expanded: BoolProperty(default=True)
    key_type: StringProperty(default='NONE')
    value: StringProperty(default='PRESS')
    ctrl: BoolProperty()
    alt: BoolProperty()
    shift: BoolProperty()
    oskey: BoolProperty()
    slots: CollectionProperty(type=GRSPIE_Slot)


class GRSPIE_Preferences(AddonPreferences):
    bl_idname = ADDON_ID

    pies: CollectionProperty(type=GRSPIE_Pie)
    initialized: BoolProperty(default=False)

    def draw_shortcut(self, context, box, pie):
        kc = context.window_manager.keyconfigs.user
        km, kmi = find_user_kmi(pie)
        col = box.column()
        col.label(text="Shortcut:")
        if kmi:
            rna_keymap_ui.draw_kmi([], kc, km, kmi, col, 0)
        else:
            col.label(text="Shortcut not available yet. Re-enable the add-on if this persists.", icon='ERROR')

    def draw_slots(self, box, pie_index, pie):
        col = box.column()
        for slot_index, slot in enumerate(pie.slots):
            slot_box = col.box()
            row = slot_box.row(align=True)
            row.label(text=SLOT_NAMES[slot_index])
            row.prop(slot, "kind", text="")

            op = row.operator("grspie.slot_move", text="", icon='TRIA_UP')
            op.pie_index = pie_index
            op.slot_index = slot_index
            op.direction = 'UP'

            op = row.operator("grspie.slot_move", text="", icon='TRIA_DOWN')
            op.pie_index = pie_index
            op.slot_index = slot_index
            op.direction = 'DOWN'

            op = row.operator("grspie.slot_remove", text="", icon='X')
            op.pie_index = pie_index
            op.slot_index = slot_index

            if slot.kind != 'EMPTY':
                sub = slot_box.row(align=True)
                sub.prop(slot, "label", text="Label")
                sub.prop(slot, "icon", text="Icon")
                slot_box.prop(slot, "command", text=COMMAND_LABELS[slot.kind])

        add_row = box.row()
        add_row.enabled = len(pie.slots) < MAX_SLOTS
        op = add_row.operator("grspie.slot_add", icon='ADD')
        op.pie_index = pie_index

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.operator("grspie.pie_add", icon='ADD')
        row.operator("grspie.config_import", icon='IMPORT')
        row.operator("grspie.config_export", icon='EXPORT')
        row.operator("grspie.reset_defaults", icon='FILE_REFRESH')

        for pie_index, pie in enumerate(self.pies):
            box = layout.box()
            header = box.row(align=True)
            header.prop(
                pie, "expanded", text="", emboss=False,
                icon='TRIA_DOWN' if pie.expanded else 'TRIA_RIGHT',
            )
            header.prop(pie, "name", text="")
            op = header.operator("grspie.pie_remove", text="", icon='X')
            op.index = pie_index

            if not pie.expanded:
                continue

            self.draw_shortcut(context, box, pie)
            self.draw_slots(box, pie_index, pie)

        help_box = layout.box()
        help_box.label(text="Slots are filled in this order: Left, Right, Bottom, Top, Top-Left, Top-Right, Bottom-Left, Bottom-Right.")
        help_box.label(text="Operator examples: transform.translate   |   object.mode_set(mode='EDIT')")
        help_box.label(text="A Pie slot can point to another custom pie by its name.")
        help_box.label(text="Enable Auto-Save Preferences to keep your pies between sessions.")


class GRSPIE_OT_pie_add(Operator):
    bl_idname = "grspie.pie_add"
    bl_label = "Add Pie"
    bl_description = "Add a new custom pie menu"

    def execute(self, context):
        prefs = get_prefs()
        fill_pie(prefs.pies.add(), {"name": "Custom Pie", "slots": []})
        rebuild()
        return {'FINISHED'}


class GRSPIE_OT_pie_remove(Operator):
    bl_idname = "grspie.pie_remove"
    bl_label = "Remove Pie"
    bl_description = "Remove this pie menu"

    index: IntProperty()

    def execute(self, context):
        prefs = get_prefs()
        if not 0 <= self.index < len(prefs.pies):
            return {'CANCELLED'}
        prefs.pies.remove(self.index)
        rebuild()
        return {'FINISHED'}


class GRSPIE_OT_slot_add(Operator):
    bl_idname = "grspie.slot_add"
    bl_label = "Add Slot"
    bl_description = "Add a new slot to this pie menu"

    pie_index: IntProperty()

    def execute(self, context):
        prefs = get_prefs()
        if not 0 <= self.pie_index < len(prefs.pies):
            return {'CANCELLED'}
        pie = prefs.pies[self.pie_index]
        if len(pie.slots) >= MAX_SLOTS:
            self.report({'WARNING'}, "A pie menu can have at most 8 slots")
            return {'CANCELLED'}
        slot = pie.slots.add()
        slot.kind = 'OPERATOR'
        return {'FINISHED'}


class GRSPIE_OT_slot_remove(Operator):
    bl_idname = "grspie.slot_remove"
    bl_label = "Remove Slot"
    bl_description = "Remove this slot"

    pie_index: IntProperty()
    slot_index: IntProperty()

    def execute(self, context):
        prefs = get_prefs()
        if not 0 <= self.pie_index < len(prefs.pies):
            return {'CANCELLED'}
        pie = prefs.pies[self.pie_index]
        if not 0 <= self.slot_index < len(pie.slots):
            return {'CANCELLED'}
        pie.slots.remove(self.slot_index)
        return {'FINISHED'}


class GRSPIE_OT_slot_move(Operator):
    bl_idname = "grspie.slot_move"
    bl_label = "Move Slot"
    bl_description = "Move this slot to another position"

    pie_index: IntProperty()
    slot_index: IntProperty()
    direction: EnumProperty(items=(('UP', "Up", ""), ('DOWN', "Down", "")))

    def execute(self, context):
        prefs = get_prefs()
        if not 0 <= self.pie_index < len(prefs.pies):
            return {'CANCELLED'}
        pie = prefs.pies[self.pie_index]
        target = self.slot_index - 1 if self.direction == 'UP' else self.slot_index + 1
        if not (0 <= self.slot_index < len(pie.slots) and 0 <= target < len(pie.slots)):
            return {'CANCELLED'}
        pie.slots.move(self.slot_index, target)
        return {'FINISHED'}


class GRSPIE_OT_reset_defaults(Operator):
    bl_idname = "grspie.reset_defaults"
    bl_label = "Reset"
    bl_description = "Remove all pies and restore the default G R S pie"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        prefs = get_prefs()
        prefs.pies.clear()
        for pie_data in DEFAULT_PIES:
            fill_pie(prefs.pies.add(), pie_data)
        prefs.initialized = True
        rebuild()
        return {'FINISHED'}


class GRSPIE_OT_config_export(Operator, ExportHelper):
    bl_idname = "grspie.config_export"
    bl_label = "Export Config"
    bl_description = "Export all pies and shortcuts to a JSON file"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        self.filepath = "grs_pie_config.json"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        prefs = get_prefs()
        data = build_config(prefs)
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as error:
            self.report({'ERROR'}, "Could not write file: " + str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, "Exported %d pie(s)" % len(data["pies"]))
        return {'FINISHED'}


class GRSPIE_OT_config_import(Operator, ImportHelper):
    bl_idname = "grspie.config_import"
    bl_label = "Import Config"
    bl_description = "Import pies and shortcuts from a JSON file"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    mode: EnumProperty(
        name="Mode",
        items=(
            ('APPEND', "Append", "Add the imported pies to the existing ones"),
            ('REPLACE', "Replace", "Remove all existing pies before importing"),
        ),
        default='APPEND',
    )

    def execute(self, context):
        prefs = get_prefs()
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, "Could not read file: " + str(error))
            return {'CANCELLED'}

        if (
            not isinstance(data, dict)
            or data.get("format") != CONFIG_FORMAT
            or not isinstance(data.get("pies"), list)
        ):
            self.report({'ERROR'}, "Not a valid GRS Pie config file")
            return {'CANCELLED'}

        if self.mode == 'REPLACE':
            prefs.pies.clear()

        count = 0
        for pie_data in data["pies"]:
            if isinstance(pie_data, dict):
                fill_pie(prefs.pies.add(), pie_data)
                count += 1

        prefs.initialized = True
        rebuild()
        self.report({'INFO'}, "Imported %d pie(s)" % count)
        return {'FINISHED'}


classes = (
    GRSPIE_Slot,
    GRSPIE_Pie,
    GRSPIE_Preferences,
    GRSPIE_OT_pie_add,
    GRSPIE_OT_pie_remove,
    GRSPIE_OT_slot_add,
    GRSPIE_OT_slot_remove,
    GRSPIE_OT_slot_move,
    GRSPIE_OT_reset_defaults,
    GRSPIE_OT_config_export,
    GRSPIE_OT_config_import,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    _attempts[0] = 0
    if not init_runtime():
        bpy.app.timers.register(deferred_init, first_interval=0.2)


def unregister():
    if bpy.app.timers.is_registered(deferred_init):
        bpy.app.timers.unregister(deferred_init)
    clear_runtime()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()

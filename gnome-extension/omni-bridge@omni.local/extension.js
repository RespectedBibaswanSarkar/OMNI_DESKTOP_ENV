import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS_NAME = 'org.omni.Bridge';
const OBJ_PATH = '/org/omni/Bridge';

const IFACE = `<node>
<interface name="org.omni.Bridge">
  <method name="Ping"><arg type="s" direction="out" name="pong"/></method>
  <method name="ListWindows"><arg type="s" direction="out" name="json"/></method>
  <method name="ActiveWindow"><arg type="s" direction="out" name="json"/></method>
  <method name="ActiveWorkspace"><arg type="i" direction="out" name="index"/></method>
  <method name="Focus"><arg type="t" direction="in" name="id"/><arg type="b" direction="out" name="ok"/></method>
  <method name="Close"><arg type="t" direction="in" name="id"/><arg type="b" direction="out" name="ok"/></method>
  <method name="MoveToWorkspace"><arg type="t" direction="in" name="id"/><arg type="i" direction="in" name="index"/><arg type="b" direction="out" name="ok"/></method>
  <method name="SwitchWorkspace"><arg type="i" direction="in" name="index"/><arg type="b" direction="out" name="ok"/></method>
</interface>
</node>`;

export default class OmniBridge extends Extension {
    enable() {
        this._impl = {
            Ping: () => 'pong',
            ListWindows: () => JSON.stringify(this._windows().map(w => this._describe(w))),
            ActiveWindow: () => {
                const w = global.display.focus_window;
                return JSON.stringify(w && this._isNormal(w) ? this._describe(w) : {});
            },
            ActiveWorkspace: () => global.workspace_manager.get_active_workspace_index(),
            Focus: id => this._withWindow(id, w => { Main.activateWindow(w); return true; }),
            Close: id => this._withWindow(id, w => { w.delete(global.get_current_time()); return true; }),
            MoveToWorkspace: (id, index) => this._withWindow(id, w => {
                if (index < 0 || index >= global.workspace_manager.get_n_workspaces())
                    return false;
                w.change_workspace_by_index(index, false);
                return true;
            }),
            SwitchWorkspace: index => {
                const ws = global.workspace_manager.get_workspace_by_index(index);
                if (!ws)
                    return false;
                ws.activate(global.get_current_time());
                return true;
            },
        };
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this._impl);
        this._dbus.export(Gio.DBus.session, OBJ_PATH);
        this._owner = Gio.bus_own_name_on_connection(Gio.DBus.session, BUS_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
    }

    disable() {
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
        if (this._owner) {
            Gio.bus_unown_name(this._owner);
            this._owner = 0;
        }
        this._impl = null;
    }

    _isNormal(w) {
        return w.get_window_type() === Meta.WindowType.NORMAL && !w.is_skip_taskbar();
    }

    _windows() {
        return global.get_window_actors().map(a => a.meta_window).filter(w => w && this._isNormal(w));
    }

    _describe(w) {
        const ws = w.get_workspace();
        return {
            id: w.get_id(),
            title: w.get_title() ?? '',
            class: w.get_wm_class() ?? '',
            app: w.get_gtk_application_id() ?? '',
            workspace: ws ? ws.index() : -1,
            pid: w.get_pid(),
            focused: global.display.focus_window === w,
        };
    }

    _withWindow(id, fn) {
        const w = this._windows().find(x => x.get_id() === Number(id));
        return w ? fn(w) : false;
    }
}

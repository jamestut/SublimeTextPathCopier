import sublime
import sublime_plugin

import os
import json
from collections import namedtuple

def _count_path_components(path):
	ret = 0
	while path != "":
		path, _ = os.path.split(path)
		ret += 1
		if path == "/":
			ret += 1
			break
	return ret

def _get_prefix(prefixes, path):
	candidates = []
	for prefix in prefixes:
		if prefix == "":
			candidates.append(prefix)
			continue
		if prefix[-1] != "/":
			prefix += "/"
		if path.startswith(prefix):
			candidates.append(prefix)

	if len(candidates) == 0:
		return None
	if len(candidates) == 1:
		return candidates[0]

	# more than 1 matches = look for the first occurence of the most specific one
	# specific as in the higher the path component count, the merrier!
	candidate = None
	candidate_len = None
	for c in candidates:
		rep = False
		new_len = _count_path_components(c)
		if candidate is None:
			rep = True
		elif new_len > candidate_len:
			rep = True
		if rep:
			candidate = c
			candidate_len = new_len
	return candidate

def _relative_path(window, path):
	prefix = _get_prefix(window.folders(), path)
	if prefix:
		return path[len(prefix):]
	return None

def _absolute_path(window, path):
	# assume supplied path is always absolute
	return path

def _mapped_path(window, path):
	mapcontent = _inst.get_contents(window)
	if not mapcontent:
		return None

	# prioritize absolute path mapping
	filteredkeys = (i for i in mapcontent.keys() if i.startswith("/"))
	pref = _get_prefix(filteredkeys, path)
	if pref is None:
		# absolute path not found? try relative mapping
		filteredkeys = (i for i in mapcontent.keys() if not i.startswith("/"))
		path = _relative_path(window, path)
		pref = _get_prefix(filteredkeys, path)
	if pref is None:
		return None

	target = mapcontent[pref[:-1] if pref.endswith("/") else pref]
	srcpath = path[len(pref):]
	if srcpath.startswith("/"):
		srcpath = srcpath[1:]

	return os.path.join(target, srcpath)

def _process_path(window, path, kind):
	if not path:
		return None
	if kind == "name":
		return os.path.split(path)[1]
	elif kind == "relative":
		return _relative_path(window, path)
	elif kind == "absolute":
		return _absolute_path(window, path)
	elif kind == "mapped":
		return _mapped_path(window, path)
	else:
		return None

def _copy_path(window, path, kind, lineno=None):
	ret = _process_path(window, path, kind)
	if ret:
		if lineno:
			ret = f"{ret}:{lineno}"
		sublime.set_clipboard(ret)

class SideBarCopyPath(sublime_plugin.WindowCommand):
	def run(self, paths, kind):
		_copy_path(self.window, self._get_path(paths), kind)

	def is_enabled(self, paths, kind):
		return bool(_process_path(self.window, self._get_path(paths), kind))

	def _get_path(self, paths):
		if paths:
			return paths[0]
		return None

class TabContextCopyPath(sublime_plugin.WindowCommand):
	def run(self, group, index, kind):
		path = self._get_path(group, index)
		_copy_path(self.window, path, kind)

	def is_enabled(self, group, index, kind):
		path = self._get_path(group, index)
		return bool(_process_path(self.window, path, kind))

	def _get_path(self, group, index):
		return self.window.views_in_group(group)[index].file_name()

class EditorContextCopyPath(sublime_plugin.TextCommand):
	def run(self, edit, kind):
		view = self.view
		sel = view.sel()
		# no selection at all = user should copy via tab/sidebar context instead
		if not sel:
			return False
		# 'rowcol' is 0-based, but we want 1-based
		lineno, _ = view.rowcol(sel[0].begin())
		_copy_path(self.view.window(), self.view.file_name(), kind, lineno + 1)

	def is_enabled(self, kind):
		if not self.view.sel():
			return False
		return bool(_process_path(self.view.window(), self.view.file_name(), kind))

class OverridePathCopierMapFile(sublime_plugin.WindowCommand):
	def run(self, clear=False):
		if clear:
			_inst.override_path(self.window, None)
			return

		def on_done(v):
			if v.strip() != "":
				_inst.override_path(self.window, v)

		self.window.show_input_panel("Path to map file", "", on_done, None, None)

class _MapFileManager():
	_WindowData = namedtuple('WindowData',
		['path', 'mtime', 'data', 'overriden'],
		defaults = [None, None, None, False])

	def __init__(self):
		self.data = {}
		self.key_name = 'path_copier_map_file'
		self.settings_name = 'PathCopier.sublime-settings'

	def get_contents(self, window):
		data = self._get_data_object(window)

		# decide when to refresh based on file name
		refresh = False
		if not data.overriden:
			newpath = self._get_path(window)
			if newpath != data.path:
				data.path = newpath
				refresh = True

		# decide to refresh based on mtime
		if not refresh:
			if data.path:
				print(data)
				new_mtime = os.stat(data.path).st_mtime
				if new_mtime != data.mtime:
					data.mtime = new_mtime
					refresh = True

		if refresh:
			self._refresh(data)

		return data.data

	def override_path(self, window, new_path):
		data = self._get_data_object(window)

		if new_path is None:
			data.overriden = False
			data.path = self._get_path(window)
		else:
			data.overriden = True
			data.path = new_path

		try:
			self._refresh(data)
		except Exception as ex:
			sublime.error_message(f"Error setting map path: {ex}")

	def _refresh(self, data):
		if data.path is None:
			data.data = None
			return

		with open(data.path, 'r') as f:
			data.data = json.load(f)

		# basic check
		for k in data.data:
			if k.endswith("/"):
				raise RuntimeError("Path key cannot end with slash")

	def _get_data_object(self, window):
		wid = window.id()
		if wid not in self.data:
			self.data[wid] = self._WindowData()
		data = self.data[wid]
		return data

	def _get_path(self, window):
		# prioritize project settings
		pjdata = sublime.active_window().project_data()
		if pjdata and self.key_name in pjdata:
			ret = pjdata[self.key_name]
			if ret:
				return ret
		# then refer to PathCopier's settings if not exist
		return sublime.load_settings(self.settings_name).get(self.key_name)

_inst = _MapFileManager()

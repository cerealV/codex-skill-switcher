import importlib.util
import json
from pathlib import Path
import tempfile
import tkinter as tk
import tomllib
import unittest
from unittest.mock import patch
import sys

MODULE = Path(__file__).resolve().parents[1] / 'skill-switcher/skill_switcher.py'
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location('switcher', MODULE)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class SkillSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        cls.base = Path(cls.temp.name)
        cls.home = cls.base / 'home'
        cls.home.mkdir()
        cls.project = cls.base / 'project'
        cls.paths = []
        for directory in ['same-name-a', 'same-name-b']:
            path = cls.project / '.agents/skills' / directory / 'SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text('---\nname: same-name\ndescription: Independent skills must be toggled by path.\n---\nTest only.\n', encoding='utf-8')
            cls.paths.append(str(path))
        cls.original = b'# keep this comment\nmodel = "existing-model"\n\n[features]\nexample = true # keep inline\n'
        (cls.home / 'config.toml').write_bytes(cls.original)
        cls.server = app.AppServer(home=cls.home)

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        cls.temp.cleanup()

    def setUp(self):
        (self.home / 'config.toml').write_bytes(self.original)

    def skills(self):
        skills, errors = self.server.skills(self.project)
        self.assertEqual(errors, [], 'Discovery errors must be surfaced, not skipped.')
        return {str(Path(s['path'])): s for s in skills}

    def test_same_name_skills_are_independent_and_switch_back_on(self):
        self.assertTrue(self.skills()[self.paths[0]]['enabled'])
        backup = app.save_changes(self.server, {self.paths[0]: False}, self.original)
        self.assertEqual(backup.read_bytes(), self.original, 'A reversible toggle needs an exact pre-write backup.')
        skills = self.skills()
        self.assertFalse(skills[self.paths[0]]['enabled'])
        self.assertTrue(skills[self.paths[1]]['enabled'], 'Disabling one path must not disable its namesake.')
        before = app.config_bytes(self.home)
        app.save_changes(self.server, {self.paths[0]: True}, before)
        self.assertTrue(self.skills()[self.paths[0]]['enabled'])
        doc = tomllib.loads(app.config_bytes(self.home).decode('utf-8'))
        overrides = doc.get('skills', {}).get('config', [])
        self.assertLessEqual(len(overrides), 1, 'Switching back may remove the override but must never duplicate it.')
        with_server = app.AppServer(home=self.home)
        try:
            reloaded, errors = with_server.skills(self.project)
            self.assertEqual(errors, [])
            self.assertTrue(next(s for s in reloaded if s['path'] == self.paths[0])['enabled'], 'The switch must persist across server restarts.')
        finally:
            with_server.close()

    def test_unrelated_settings_and_comments_survive(self):
        app.save_changes(self.server, dict.fromkeys(self.paths, False), self.original)
        text = app.config_bytes(self.home).decode('utf-8')
        self.assertIn('# keep this comment', text)
        self.assertIn('# keep inline', text)
        doc = tomllib.loads(text)
        self.assertEqual(doc['model'], 'existing-model')
        self.assertTrue(doc['features']['example'])
        self.assertTrue(all(not self.skills()[p]['enabled'] for p in self.paths))

    def test_concurrent_external_edit_is_not_overwritten(self):
        external = self.original + b'\n# changed elsewhere\n'
        (self.home / 'config.toml').write_bytes(external)
        with self.assertRaisesRegex(RuntimeError, '其他程序修改'):
            app.save_changes(self.server, {self.paths[0]: False}, self.original)
        self.assertEqual(app.config_bytes(self.home), external)

    def test_no_changes_does_not_create_a_backup_or_write(self):
        before = set(self.home.glob('*.bak'))
        self.assertIsNone(app.save_changes(self.server, {}, self.original))
        self.assertEqual(app.config_bytes(self.home), self.original)
        self.assertEqual(set(self.home.glob('*.bak')), before)

    def test_installed_app_does_not_use_internal_bundle_as_project(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(sys, 'frozen', True, create=True):
                ui = app.Switcher(root, self.server)
            self.assertEqual(ui.cwd, Path.home(), 'The installed app must discover user skills, not treat bundled runtime files as a project.')
        finally:
            root.update_idletasks()
            root.destroy()

    def test_partial_failure_reports_exact_completed_count(self):
        real = self.server
        class FailSecondWrite:
            home = real.home
            calls = 0
            def call(self, method, params):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError('test failure')
                return real.call(method, params)
        with self.assertRaisesRegex(RuntimeError, '1/2'):
            app.save_changes(FailSecondWrite(), dict.fromkeys(self.paths, False), self.original)
        skills = self.skills()
        self.assertFalse(skills[self.paths[0]]['enabled'])
        self.assertTrue(skills[self.paths[1]]['enabled'])

    def test_window_stages_changes_and_search_does_not_drop_them(self):
        root = tk.Tk()
        root.withdraw()
        try:
            ui = app.Switcher(root, self.server)
            ui.cwd = self.project
            ui.load()
            total = len(ui.skills)
            enabled = sum(s['enabled'] for s in ui.skills.values())
            self.assertEqual(ui.count.get(), f'已开启 {enabled} / 共 {total} 个 Skill')
            parents = ui.tree.get_children()
            self.assertTrue(parents)
            for parent in parents:
                self.assertFalse(ui.tree.item(parent, 'open'), 'Initial source directories must be collapsed.')
                for child in ui.tree.get_children(parent):
                    self.assertFalse(ui.tree.item(child, 'open'), 'Initial purpose directories must be collapsed.')
            child = ui.tree.parent(self.paths[0])
            parent = ui.tree.parent(child)
            ui.tree.selection_set(parent)
            ui.toggle()
            self.assertFalse(ui.pending, 'Category rows must not change any skill switches.')
            ui.tree.item(parent, open=True)
            ui.tree.item(child, open=True)
            ui.tree.selection_set(self.paths[0])
            ui.toggle()
            self.assertEqual(ui.pending, {self.paths[0]: False})
            staged_count = f'已开启 {enabled - 1} / 共 {total} 个 Skill'
            self.assertEqual(ui.count.get(), staged_count)
            self.assertIn('待保存 1 项', ui.count_note.get())
            self.assertTrue(ui.tree.item(parent, 'open'), 'Toggling must preserve browsing position.')
            self.assertEqual(app.config_bytes(self.home), self.original, 'Clicking a switch must stage until Save.')
            ui.query.set('no match')
            self.assertEqual(len(ui.tree.get_children()), 0)
            self.assertEqual(ui.count.get(), staged_count, 'Search must not turn the global count into a filtered count.')
            ui.query.set('same-name')
            self.assertTrue(ui.tree.item(child, 'open'), 'Search should expose matching leaves.')
            ui.query.set('')
            self.assertTrue(ui.tree.item(parent, 'open'))
            self.assertTrue(ui.tree.item(child, 'open'))
            ui.tree.item(parent, open=False)
            ui.tree.item(child, open=False)
            ui.query.set('same-name')
            ui.query.set('')
            self.assertFalse(ui.tree.item(parent, 'open'), 'Clearing search must restore prior collapsed directories.')
            self.assertFalse(ui.tree.item(child, 'open'))
            ui.filter.set('已开启')
            self.assertEqual(ui.count.get(), staged_count, 'Status filters must retain the global count.')
            ui.filter.set('全部状态')
            ui.grouping.set('用途 → GitHub 来源')
            self.assertTrue(all(not ui.tree.item(p, 'open') for p in ui.tree.get_children()))
            self.assertEqual(ui.count.get(), staged_count)
            self.assertEqual(ui.pending, {self.paths[0]: False})
            ui.tree.selection_set(self.paths[0])
            ui.toggle()
            self.assertFalse(ui.pending, 'Returning to the original state should cancel the staged change.')
            self.assertEqual(ui.count.get(), f'已开启 {enabled} / 共 {total} 个 Skill')
            self.assertNotIn('待保存', ui.count_note.get())
            ui.toggle()
            self.assertTrue(ui.save(notify=False), 'Saving must reload and verify the effective switch state.')
            self.assertFalse(ui.pending)
            self.assertEqual(ui.count.get(), staged_count)
            self.assertEqual(ui.count_note.get(), '已保存状态 · 统计全部 Skill')
            root.update_idletasks()
        finally:
            root.destroy()

    def test_bulk_scope_includes_hidden_members_and_respects_group_boundaries(self):
        root = tk.Tk()
        root.withdraw()
        try:
            ui = app.Switcher(root, self.server)
            fixtures = [
                ('a', 'repo-a', '开发工程与测试', True),
                ('b', 'repo-a', '开发工程与测试', False),
                ('c', 'repo-a', '界面设计与前端', True),
                ('d', 'repo-b', '开发工程与测试', True),
            ]
            ui.skills = {path: dict(path=path, name='same-name', enabled=enabled, purpose=purpose,
                                    source_group=source, source_evidence='test')
                         for path, source, purpose, enabled in fixtures}
            ui.render()
            parent = ui.tree.parent(ui.tree.parent('a'))
            child = ui.tree.parent('a')
            ui.query.set('repo-a')
            ui.filter.set('已开启')
            ui.tree.selection_set(child)
            ui.set_bulk(False, selected_group=True)
            self.assertEqual(ui.pending, {'a': False}, 'A nested category must not affect another purpose or repository.')
            ui.filter.set('全部状态')
            ui.query.set('')
            ui.tree.selection_set(parent)
            ui.set_bulk(False, selected_group=True)
            self.assertEqual(ui.pending, {'a': False, 'c': False}, 'A source category must include all nested purposes but leave other sources alone.')
            ui.query.set('界面设计与前端')
            ui.tree.selection_set(parent)
            ui.set_bulk(True, selected_group=True)
            self.assertEqual(ui.pending, {'b': True}, 'Enabling a category must include hidden disabled members and cancel obsolete staged edits.')
            ui.query.set('no match')
            ui.set_bulk(False)
            self.assertEqual(ui.pending, {'a': False, 'c': False, 'd': False}, 'All skills means the full project list even when the search has no results.')
            self.assertEqual(ui.count.get(), '已开启 0 / 共 4 个 Skill')
            ui.set_bulk(True)
            self.assertEqual(ui.pending, {'b': True})
            self.assertEqual(ui.count.get(), '已开启 4 / 共 4 个 Skill')
            ui.query.set('')
            ui.grouping.set('用途 → GitHub 来源')
            ui.tree.selection_set(ui.tree.parent(ui.tree.parent('a')))
            ui.set_bulk(False, selected_group=True)
            self.assertEqual(ui.pending, {'a': False, 'd': False}, 'Purpose grouping must span its repositories while keeping other purposes intact.')
            self.assertEqual(app.config_bytes(self.home), self.original, 'Bulk clicks must stage changes until Save.')
        finally:
            root.update_idletasks()
            root.destroy()

    def test_collapsed_group_bulk_changes_save_and_reload(self):
        root = tk.Tk()
        root.withdraw()
        try:
            ui = app.Switcher(root, self.server)
            ui.cwd = self.project
            ui.load()
            # Keep this save test scoped to skills created in the temporary project.
            ui.skills = {p: ui.skills[p] for p in self.paths}
            ui.render()
            parent = ui.tree.parent(ui.tree.parent(self.paths[0]))
            self.assertEqual(set(ui.group_members[parent]), set(self.paths))
            self.assertFalse(ui.tree.item(parent, 'open'))
            ui.tree.selection_set(parent)
            ui.set_bulk(False, selected_group=True)
            self.assertEqual(ui.pending, dict.fromkeys(self.paths, False))
            self.assertFalse(ui.tree.item(parent, 'open'), 'Bulk switching must work without opening a collapsed directory.')
            self.assertTrue(ui.save(notify=False))
            self.assertTrue(all(not ui.skills[p]['enabled'] for p in self.paths))
            self.assertFalse(ui.pending)
            self.assertEqual(next(self.home.glob('*.bak')).read_bytes(), self.original)
            ui.tree.selection_set(parent)
            ui.set_bulk(True, selected_group=True)
            self.assertTrue(ui.save(notify=False))
            self.assertTrue(all(ui.skills[p]['enabled'] for p in self.paths))
        finally:
            root.update_idletasks()
            root.destroy()


class FooterLayoutTests(unittest.TestCase):
    def test_footer_remains_visible_at_small_sizes_and_windows_scaling(self):
        for scale in (1, 1.5, 2):
            with self.subTest(scale=scale):
                root = tk.Tk()
                root.withdraw()
                try:
                    root.tk.call('tk', 'scaling', scale * 96 / 72)
                    ui = app.Switcher(root, None)
                    for timer in root.tk.call('after', 'info'):
                        root.after_cancel(timer)
                    ui.render()
                    for size in ('1200x760', '820x560'):
                        root.geometry(size)
                        root.deiconify()
                        root.update()
                        buttons = ui.save_button.master.winfo_children()
                        self.assertEqual(len(buttons), 3)
                        for button in buttons:
                            self.assertTrue(button.winfo_ismapped(), 'All footer actions must stay visible when the table runs out of space.')
                            self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight(), 'Button text must retain its full requested height.')
                            self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth(), 'Button labels must not be shortened by the status text.')
                            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), root.winfo_rooty() + root.winfo_height())
                        self.assertEqual(str(ui.save_button['state']), 'disabled')
                finally:
                    root.update_idletasks()
                    root.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)

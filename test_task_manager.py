"""Unit tests for task_manager.TaskManager.

Run with:  python test_task_manager.py
"""

import os
import tempfile
import unittest

from task_manager import TaskManager


class TaskManagerTests(unittest.TestCase):

    def setUp(self):
        # Each test gets a fresh temp file so tests are isolated.
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)  # start with no file
        self.tm = TaskManager(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    # ------------------------------------------------------------------ #
    # create
    # ------------------------------------------------------------------ #
    def test_create_task(self):
        t = self.tm.create_task("My task", "desc", "high")
        self.assertEqual(t["id"], 1)
        self.assertEqual(t["title"], "My task")
        self.assertEqual(t["description"], "desc")
        self.assertEqual(t["priority"], "high")
        self.assertEqual(t["status"], "pending")
        self.assertIn("created_at", t)
        # second task gets id 2
        t2 = self.tm.create_task("Second")
        self.assertEqual(t2["id"], 2)
        self.assertEqual(t2["description"], "")
        self.assertEqual(t2["priority"], "medium")

    def test_create_task_empty_title(self):
        with self.assertRaises(ValueError):
            self.tm.create_task("")
        with self.assertRaises(ValueError):
            self.tm.create_task("   ")

    def test_create_task_invalid_priority(self):
        with self.assertRaises(ValueError):
            self.tm.create_task("Title", priority="urgent")

    # ------------------------------------------------------------------ #
    # edit
    # ------------------------------------------------------------------ #
    def test_edit_task(self):
        t = self.tm.create_task("Original", "old desc", "low")
        edited = self.tm.edit_task(t["id"], title="New title",
                                   description="new desc", priority="high")
        self.assertEqual(edited["title"], "New title")
        self.assertEqual(edited["description"], "new desc")
        self.assertEqual(edited["priority"], "high")
        # unchanged fields preserved
        self.assertEqual(edited["id"], t["id"])
        self.assertEqual(edited["status"], "pending")
        self.assertEqual(edited["created_at"], t["created_at"])

    def test_edit_task_partial(self):
        t = self.tm.create_task("Original", "desc", "medium")
        edited = self.tm.edit_task(t["id"], priority="high")
        self.assertEqual(edited["title"], "Original")  # unchanged
        self.assertEqual(edited["priority"], "high")

    def test_edit_task_not_found(self):
        with self.assertRaises(KeyError):
            self.tm.edit_task(999, title="x")

    def test_edit_task_empty_title(self):
        t = self.tm.create_task("Original")
        with self.assertRaises(ValueError):
            self.tm.edit_task(t["id"], title="   ")

    # ------------------------------------------------------------------ #
    # complete
    # ------------------------------------------------------------------ #
    def test_complete_task(self):
        t = self.tm.create_task("Do something")
        completed = self.tm.complete_task(t["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.tm.get_task(t["id"])["status"], "completed")

    def test_complete_task_not_found(self):
        with self.assertRaises(KeyError):
            self.tm.complete_task(999)

    # ------------------------------------------------------------------ #
    # delete
    # ------------------------------------------------------------------ #
    def test_delete_task(self):
        t = self.tm.create_task("To delete")
        deleted = self.tm.delete_task(t["id"])
        self.assertEqual(deleted["id"], t["id"])
        self.assertIsNone(self.tm.get_task(t["id"]))
        self.assertEqual(len(self.tm.list_tasks()), 0)

    def test_delete_task_not_found(self):
        with self.assertRaises(KeyError):
            self.tm.delete_task(999)

    # ------------------------------------------------------------------ #
    # search
    # ------------------------------------------------------------------ #
    def test_search_tasks(self):
        self.tm.create_task("Buy groceries", "Milk and eggs")
        self.tm.create_task("Write report", "Quarterly summary")
        self.tm.create_task("Call client", "Discuss groceries order")

        results = self.tm.search_tasks("groceries")
        self.assertEqual(len(results), 2)  # title + description match

        results = self.tm.search_tasks("report")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Write report")

    def test_search_tasks_case_insensitive(self):
        self.tm.create_task("Buy Groceries")
        results = self.tm.search_tasks("groceries")
        self.assertEqual(len(results), 1)

    def test_search_no_results(self):
        self.tm.create_task("Something")
        self.assertEqual(self.tm.search_tasks("nonexistent"), [])

    def test_search_empty_query(self):
        self.tm.create_task("Something")
        self.assertEqual(self.tm.search_tasks(""), [])

    # ------------------------------------------------------------------ #
    # persistence
    # ------------------------------------------------------------------ #
    def test_persistence(self):
        t = self.tm.create_task("Persisted", "saved desc", "high")
        self.tm.complete_task(t["id"])

        # New manager, same file -> should reload from disk.
        tm2 = TaskManager(self.path)
        loaded = tm2.get_task(t["id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["title"], "Persisted")
        self.assertEqual(loaded["description"], "saved desc")
        self.assertEqual(loaded["priority"], "high")
        self.assertEqual(loaded["status"], "completed")

    def test_persistence_after_delete(self):
        t1 = self.tm.create_task("Keep")
        t2 = self.tm.create_task("Remove")
        self.tm.delete_task(t2["id"])

        tm2 = TaskManager(self.path)
        self.assertEqual(len(tm2.list_tasks()), 1)
        self.assertIsNotNone(tm2.get_task(t1["id"]))
        self.assertIsNone(tm2.get_task(t2["id"]))

    # ------------------------------------------------------------------ #
    # get / list
    # ------------------------------------------------------------------ #
    def test_get_task(self):
        t = self.tm.create_task("Find me")
        self.assertEqual(self.tm.get_task(t["id"]), t)
        self.assertIsNone(self.tm.get_task(999))

    def test_list_tasks(self):
        t1 = self.tm.create_task("One")
        t2 = self.tm.create_task("Two")
        tasks = self.tm.list_tasks()
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["id"], t1["id"])
        self.assertEqual(tasks[1]["id"], t2["id"])

    def test_list_tasks_empty(self):
        self.assertEqual(self.tm.list_tasks(), [])

    # ------------------------------------------------------------------ #
    # id management
    # ------------------------------------------------------------------ #
    def test_auto_increment_after_delete(self):
        t1 = self.tm.create_task("First")
        t2 = self.tm.create_task("Second")
        self.tm.delete_task(t2["id"])
        t3 = self.tm.create_task("Third")
        # ID should not be reused.
        self.assertEqual(t3["id"], 3)
        self.assertNotEqual(t3["id"], t2["id"])

    def test_auto_increment_after_reload(self):
        t1 = self.tm.create_task("First")
        t2 = self.tm.create_task("Second")
        tm2 = TaskManager(self.path)
        t3 = tm2.create_task("Third")
        self.assertEqual(t3["id"], 3)


if __name__ == "__main__":
    unittest.main()
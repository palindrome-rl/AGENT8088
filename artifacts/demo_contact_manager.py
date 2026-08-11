"""End-to-end demo for ContactManager."""
import os, json
from contact_manager import ContactManager

DEMO_FILE = 'contacts_demo.json'

# Clean start
if os.path.exists(DEMO_FILE):
    os.remove(DEMO_FILE)

mgr = ContactManager(DEMO_FILE)

print('=== Adding 3 contacts ===')
c1 = mgr.add_contact('Alice Smith', '555-1111', 'alice@example.com')
c2 = mgr.add_contact('Bob Jones', '555-2222', 'bob@example.com')
c3 = mgr.add_contact('Carol White', '555-3333', 'carol@example.com')
for c in mgr.list_contacts():
    print(f"  [{c['id']}] {c['name']} | {c['phone']} | {c['email']}")

print('\n=== Editing contact 1 (Alice -> Alice Brown, new phone) ===')
mgr.edit_contact(1, name='Alice Brown', phone='555-9999')
print(f"  {mgr.get_contact(1)}")

print('\n=== Deleting contact 2 (Bob) ===')
mgr.delete_contact(2)
print(f"  Remaining: {len(mgr.list_contacts())} contacts")

print('\n=== Searching for "Alice" ===')
results = mgr.search_contacts('Alice')
for r in results:
    print(f"  Found: {r['name']} | {r['phone']}")

print('\n=== Searching by phone "3333" ===')
results = mgr.search_contacts('3333')
for r in results:
    print(f"  Found: {r['name']} | {r['phone']}")

print('\n=== Reloading JSON from file ===')
mgr2 = ContactManager(DEMO_FILE)
print(f"  Contacts after reload: {len(mgr2.list_contacts())}")
for c in mgr2.list_contacts():
    print(f"  [{c['id']}] {c['name']} | {c['phone']} | {c['email']}")

print('\n=== Verifying persistence ===')
assert mgr2.get_contact(1)['name'] == 'Alice Brown', 'Edit not persisted!'
assert mgr2.get_contact(1)['phone'] == '555-9999', 'Phone edit not persisted!'
assert mgr2.get_contact(2) is None, 'Deleted contact should not exist!'
assert mgr2.get_contact(3)['name'] == 'Carol White', 'Carol missing!'
assert len(mgr2.list_contacts()) == 2, 'Should have 2 contacts after delete!'
print('  All persistence checks PASSED!')

# Cleanup
os.remove(DEMO_FILE)
print('\n=== Demo complete ===')
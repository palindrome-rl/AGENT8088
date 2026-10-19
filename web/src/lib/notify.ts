// A desktop notification when a task finishes while the person is in another
// window or tab, so they need not keep checking (feature suggestion: notify on
// completion). Browsers allow the permission prompt only from a user action, so
// it is asked on the first send, and never again once answered.

export function askNotifyPermission(): void {
  try {
    if ('Notification' in window && Notification.permission === 'default') {
      void Notification.requestPermission()
    }
  } catch {
    // Unsupported or blocked: finishing silently is the old behaviour.
  }
}

export function notifyTaskDone(chatName: string): void {
  try {
    if (!('Notification' in window) || Notification.permission !== 'granted') return
    if (!document.hidden && document.hasFocus()) return
    const note = new Notification('Agent8088 finished', {
      body: chatName ? `"${chatName}" is ready.` : 'Your task is ready.',
      tag: 'agent8088-task-done',
    })
    note.onclick = () => {
      window.focus()
      note.close()
    }
  } catch {
    // A notification is a convenience; it must never break the chat.
  }
}

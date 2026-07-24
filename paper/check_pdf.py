import fitz
doc = fitz.open('/home/amir/agent8088/paper/8088_agent_paper_draft.pdf')
print(f'Pages: {doc.page_count}')
for i in range(doc.page_count):
    text = doc[i].get_text()[:400]
    print(f'--- Page {i+1} ---')
    print(text)
    print('...')

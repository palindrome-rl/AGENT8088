import { test, expect } from '@playwright/test'
test('taken topic name retries and sends chat in the new session', async ({page}) => {
  const names:string[]=[]
  await page.route('**/api/**', async route => {
    const path=new URL(route.request().url()).pathname
    if(path==='/api/sessions/new') {
      names.push(route.request().postDataJSON().name)
      return route.fulfill({status:names.length===1?409:200,json:names.length===1?{error:'session already exists'}:{name:names.at(-1),id:'a'.repeat(32),ok:true}})
    }
    await route.fulfill({json:path==='/api/status'?{session_name:'',permission_mode:'readonly'}:path==='/api/history'?{messages:[]}:path==='/api/providers'?{configured:[],builtins:[],active:'',details:{}}:[]})
  })
  await page.addInitScript(()=>{
    class Socket {static OPEN=1;readyState=1;onopen:any;onmessage:any;onclose:any;
      constructor(){setTimeout(()=>this.onopen?.({}),0)}
      send(data:string){((window as any).__sent??=[]).push(JSON.parse(data))}
      close(){this.readyState=3}
    }
    ;(window as any).WebSocket=Socket
  })
  await page.goto('/')
  await page.getByRole('textbox',{name:'Prompt'}).fill('Review session collision')
  await page.getByRole('button',{name:'Send',exact:true}).click()
  await expect.poll(()=>names.length).toBe(2)
  expect(names[1]).not.toBe(names[0])
  await expect.poll(()=>page.evaluate(()=>((window as any).__sent??[]).filter((x:any)=>x.type==='chat').length)).toBe(1)
  expect(await page.evaluate(()=>((window as any).__sent??[]).find((x:any)=>x.type==='chat').session_id)).toBe('a'.repeat(32))
  await expect(page.getByText('Could not create a unique session name',{exact:false})).toHaveCount(0)
})

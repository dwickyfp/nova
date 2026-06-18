1|import { useState } from 'react'
2|import { Fragment } from 'react/jsx-runtime'
3|import { format } from 'date-fns'
4|import {
5|  ArrowLeft,
6|  MoreVertical,
7|  Edit,
8|  Paperclip,
9|  Phone,
10|  ImagePlus,
11|  Plus,
12|  Search as SearchIcon,
13|  Send,
14|  Video,
15|  MessagesSquare,
16|} from 'lucide-react'
17|import { cn, getDisplayNameInitials } from '@/lib/utils'
18|import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
19|import { Button } from '@/components/ui/button'
20|import { ScrollArea } from '@/components/ui/scroll-area'
21|import { Separator } from '@/components/ui/separator'
23|import { Header } from '@/components/layout/header'
24|import { Main } from '@/components/layout/main'
26|import { Search } from '@/components/search'
28|import { NewChat } from './components/new-chat'
29|import { type ChatUser, type Convo } from './data/chat-types'
30|// Fake Data
31|import { conversations } from './data/convo.json'
32|
33|export function Chats() {
34|  const [search, setSearch] = useState('')
35|  const [selectedUser, setSelectedUser] = useState<ChatUser | null>(null)
36|  const [mobileSelectedUser, setMobileSelectedUser] = useState<ChatUser | null>(
37|    null
38|  )
39|  const [createConversationDialogOpened, setCreateConversationDialog] =
40|    useState(false)
41|
42|  // Filtered data based on the search query
43|  const filteredChatList = conversations.filter(({ fullName }) =>
44|    fullName.toLowerCase().includes(search.trim().toLowerCase())
45|  )
46|
47|  const currentMessage = selectedUser?.messages.reduce(
48|    (acc: Record<string, Convo[]>, obj) => {
49|      const key = format(obj.timestamp, 'd MMM, yyyy')
50|
51|      // Create an array for the category if it doesn't exist
52|      if (!acc[key]) {
53|        acc[key] = []
54|      }
55|
56|      // Push the current object to the array
57|      acc[key].push(obj)
58|
59|      return acc
60|    },
61|    {}
62|  )
63|
64|  const users = conversations.map(({ messages, ...user }) => user)
65|
66|  return (
67|    <>
68|      {/* ===== Top Heading ===== */}
69|      <Header>
70|        <Search className='me-auto' />
71|72|73|74|      </Header>
75|
76|      <Main fixed>
77|        <section className='flex h-full gap-6'>
78|          {/* Left Side */}
79|          <div className='flex w-full flex-col gap-2 sm:w-56 lg:w-72 2xl:w-80'>
80|            <div className='sticky top-0 z-10 -mx-4 bg-background px-4 pb-3 shadow-md sm:static sm:z-auto sm:mx-0 sm:p-0 sm:shadow-none'>
81|              <div className='flex items-center justify-between py-2'>
82|                <div className='flex gap-2'>
83|                  <h1 className='text-2xl font-bold'>Inbox</h1>
84|                  <MessagesSquare size={20} />
85|                </div>
86|
87|                <Button
88|                  size='icon'
89|                  variant='ghost'
90|                  onClick={() => setCreateConversationDialog(true)}
91|                  className='rounded-lg'
92|                >
93|                  <Edit size={24} className='stroke-muted-foreground' />
94|                </Button>
95|              </div>
96|
97|              <label
98|                className={cn(
99|                  'focus-within:ring-1 focus-within:ring-ring focus-within:outline-hidden',
100|                  'flex h-10 w-full items-center space-x-0 rounded-md border border-border ps-2'
101|                )}
102|              >
103|                <SearchIcon size={15} className='me-2 stroke-slate-500' />
104|                <span className='sr-only'>Search</span>
105|                <input
106|                  type='text'
107|                  className='w-full flex-1 bg-inherit text-sm focus-visible:outline-hidden'
108|                  placeholder='Search chat...'
109|                  value={search}
110|                  onChange={(e) => setSearch(e.target.value)}
111|                />
112|              </label>
113|            </div>
114|
115|            <ScrollArea className='-mx-3 h-full overflow-scroll p-3'>
116|              {filteredChatList.map((chatUsr) => {
117|                const { id, profile, username, messages, fullName } = chatUsr
118|                const lastConvo = messages[0]
119|                const lastMsg =
120|                  lastConvo.sender === 'You'
121|                    ? `You: ${lastConvo.message}`
122|                    : lastConvo.message
123|                return (
124|                  <Fragment key={id}>
125|                    <button
126|                      type='button'
127|                      className={cn(
128|                        'group hover:bg-accent hover:text-accent-foreground',
129|                        `flex w-full rounded-md px-2 py-2 text-start text-sm`,
130|                        selectedUser?.id === id && 'sm:bg-muted'
131|                      )}
132|                      onClick={() => {
133|                        setSelectedUser(chatUsr)
134|                        setMobileSelectedUser(chatUsr)
135|                      }}
136|                    >
137|                      <div className='flex gap-2'>
138|                        <Avatar>
139|                          <AvatarImage src={profile} alt={username} />
140|                          <AvatarFallback>
141|                            {getDisplayNameInitials(fullName)}
142|                          </AvatarFallback>
143|                        </Avatar>
144|                        <div>
145|                          <span className='col-start-2 row-span-2 font-medium'>
146|                            {fullName}
147|                          </span>
148|                          <span className='col-start-2 row-span-2 row-start-2 line-clamp-2 text-ellipsis text-muted-foreground group-hover:text-accent-foreground/90'>
149|                            {lastMsg}
150|                          </span>
151|                        </div>
152|                      </div>
153|                    </button>
154|                    <Separator className='my-1' />
155|                  </Fragment>
156|                )
157|              })}
158|            </ScrollArea>
159|          </div>
160|
161|          {/* Right Side */}
162|          {selectedUser ? (
163|            <div
164|              className={cn(
165|                'absolute inset-0 start-full z-50 hidden w-full flex-1 flex-col border bg-background shadow-xs sm:static sm:z-auto sm:flex sm:rounded-md',
166|                mobileSelectedUser && 'inset-s-0 flex'
167|              )}
168|            >
169|              {/* Top Part */}
170|              <div className='mb-1 flex flex-none justify-between bg-card p-4 shadow-lg sm:rounded-t-md'>
171|                {/* Left */}
172|                <div className='flex gap-3'>
173|                  <Button
174|                    size='icon'
175|                    variant='ghost'
176|                    className='-ms-2 h-full sm:hidden'
177|                    onClick={() => setMobileSelectedUser(null)}
178|                  >
179|                    <ArrowLeft className='rtl:rotate-180' />
180|                  </Button>
181|                  <div className='flex items-center gap-2 lg:gap-4'>
182|                    <Avatar className='size-9 lg:size-11'>
183|                      <AvatarImage
184|                        src={selectedUser.profile}
185|                        alt={selectedUser.username}
186|                      />
187|                      <AvatarFallback>
188|                        {getDisplayNameInitials(selectedUser.fullName)}
189|                      </AvatarFallback>
190|                    </Avatar>
191|                    <div>
192|                      <span className='col-start-2 row-span-2 text-sm font-medium lg:text-base'>
193|                        {selectedUser.fullName}
194|                      </span>
195|                      <span className='col-start-2 row-span-2 row-start-2 line-clamp-1 block max-w-32 text-xs text-nowrap text-ellipsis text-muted-foreground lg:max-w-none lg:text-sm'>
196|                        {selectedUser.title}
197|                      </span>
198|                    </div>
199|                  </div>
200|                </div>
201|
202|                {/* Right */}
203|                <div className='-me-1 flex items-center gap-1 lg:gap-2'>
204|                  <Button
205|                    size='icon'
206|                    variant='ghost'
207|                    className='hidden size-8 rounded-full sm:inline-flex lg:size-10'
208|                  >
209|                    <Video size={22} className='stroke-muted-foreground' />
210|                  </Button>
211|                  <Button
212|                    size='icon'
213|                    variant='ghost'
214|                    className='hidden size-8 rounded-full sm:inline-flex lg:size-10'
215|                  >
216|                    <Phone size={22} className='stroke-muted-foreground' />
217|                  </Button>
218|                  <Button
219|                    size='icon'
220|                    variant='ghost'
221|                    className='h-10 rounded-md sm:h-8 sm:w-4 lg:h-10 lg:w-6'
222|                  >
223|                    <MoreVertical className='stroke-muted-foreground sm:size-5' />
224|                  </Button>
225|                </div>
226|              </div>
227|
228|              {/* Conversation */}
229|              <div className='flex flex-1 flex-col gap-2 rounded-md px-4 pt-0 pb-4'>
230|                <div className='flex size-full flex-1'>
231|                  <div className='chat-text-container relative -me-4 flex flex-1 flex-col overflow-y-hidden'>
232|                    <div className='chat-flex flex h-40 w-full grow flex-col-reverse justify-start gap-4 overflow-y-auto py-2 pe-4 pb-4'>
233|                      {currentMessage &&
234|                        Object.keys(currentMessage).map((key) => (
235|                          <Fragment key={key}>
236|                            {currentMessage[key].map((msg, index) => (
237|                              <div
238|                                key={`${msg.sender}-${msg.timestamp}-${index}`}
239|                                className={cn(
240|                                  'chat-box max-w-72 px-3 py-2 wrap-break-word shadow-lg',
241|                                  msg.sender === 'You'
242|                                    ? 'self-end rounded-[16px_16px_0_16px] bg-primary/90 text-primary-foreground/75'
243|                                    : 'self-start rounded-[16px_16px_16px_0] bg-muted'
244|                                )}
245|                              >
246|                                {msg.message}{' '}
247|                                <span
248|                                  className={cn(
249|                                    'mt-1 block text-xs font-light text-foreground/75 italic',
250|                                    msg.sender === 'You' &&
251|                                      'text-end text-primary-foreground/85'
252|                                  )}
253|                                >
254|                                  {format(msg.timestamp, 'h:mm a')}
255|                                </span>
256|                              </div>
257|                            ))}
258|                            <div className='text-center text-xs'>{key}</div>
259|                          </Fragment>
260|                        ))}
261|                    </div>
262|                  </div>
263|                </div>
264|                <form className='flex w-full flex-none gap-2'>
265|                  <div className='flex flex-1 items-center gap-2 rounded-md border border-input bg-card px-2 py-1 focus-within:ring-1 focus-within:ring-ring focus-within:outline-hidden lg:gap-4'>
266|                    <div className='space-x-1'>
267|                      <Button
268|                        size='icon'
269|                        type='button'
270|                        variant='ghost'
271|                        className='h-8 rounded-md'
272|                      >
273|                        <Plus size={20} className='stroke-muted-foreground' />
274|                      </Button>
275|                      <Button
276|                        size='icon'
277|                        type='button'
278|                        variant='ghost'
279|                        className='hidden h-8 rounded-md lg:inline-flex'
280|                      >
281|                        <ImagePlus
282|                          size={20}
283|                          className='stroke-muted-foreground'
284|                        />
285|                      </Button>
286|                      <Button
287|                        size='icon'
288|                        type='button'
289|                        variant='ghost'
290|                        className='hidden h-8 rounded-md lg:inline-flex'
291|                      >
292|                        <Paperclip
293|                          size={20}
294|                          className='stroke-muted-foreground'
295|                        />
296|                      </Button>
297|                    </div>
298|                    <label className='flex-1'>
299|                      <span className='sr-only'>Chat Text Box</span>
300|                      <input
301|                        type='text'
302|                        placeholder='Type your messages...'
303|                        className='h-8 w-full bg-inherit focus-visible:outline-hidden'
304|                      />
305|                    </label>
306|                    <Button
307|                      variant='ghost'
308|                      size='icon'
309|                      className='hidden sm:inline-flex'
310|                    >
311|                      <Send size={20} />
312|                    </Button>
313|                  </div>
314|                  <Button className='h-full sm:hidden'>
315|                    <Send size={18} /> Send
316|                  </Button>
317|                </form>
318|              </div>
319|            </div>
320|          ) : (
321|            <div
322|              className={cn(
323|                'absolute inset-0 start-full z-50 hidden w-full flex-1 flex-col justify-center rounded-md border bg-card shadow-xs sm:static sm:z-auto sm:flex'
324|              )}
325|            >
326|              <div className='flex flex-col items-center space-y-6'>
327|                <div className='flex size-16 items-center justify-center rounded-full border-2 border-border'>
328|                  <MessagesSquare className='size-8' />
329|                </div>
330|                <div className='space-y-2 text-center'>
331|                  <h1 className='text-xl font-semibold'>Your messages</h1>
332|                  <p className='text-sm text-muted-foreground'>
333|                    Send a message to start a chat.
334|                  </p>
335|                </div>
336|                <Button onClick={() => setCreateConversationDialog(true)}>
337|                  Send message
338|                </Button>
339|              </div>
340|            </div>
341|          )}
342|        </section>
343|        <NewChat
344|          users={users}
345|          onOpenChange={setCreateConversationDialog}
346|          open={createConversationDialogOpened}
347|        />
348|      </Main>
349|    </>
350|  )
351|}
352|
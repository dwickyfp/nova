1|import { type ChangeEvent, useState } from 'react'
2|import { getRouteApi } from '@tanstack/react-router'
3|import { SlidersHorizontal, ArrowUpAZ, ArrowDownAZ } from 'lucide-react'
4|import { Button } from '@/components/ui/button'
5|import { Input } from '@/components/ui/input'
6|import {
7|  Select,
8|  SelectContent,
9|  SelectItem,
10|  SelectTrigger,
11|  SelectValue,
12|} from '@/components/ui/select'
13|import { Separator } from '@/components/ui/separator'
15|import { Header } from '@/components/layout/header'
16|import { Main } from '@/components/layout/main'
18|import { Search } from '@/components/search'
20|import { apps } from './data/apps'
21|
22|const route = getRouteApi('/_authenticated/apps/')
23|
24|type AppType = 'all' | 'connected' | 'notConnected'
25|
26|const appText = new Map<AppType, string>([
27|  ['all', 'All Apps'],
28|  ['connected', 'Connected'],
29|  ['notConnected', 'Not Connected'],
30|])
31|
32|export function Apps() {
33|  const {
34|    filter = '',
35|    type = 'all',
36|    sort: initSort = 'asc',
37|  } = route.useSearch()
38|  const navigate = route.useNavigate()
39|
40|  const [sort, setSort] = useState(initSort)
41|  const [appType, setAppType] = useState(type)
42|  const [searchTerm, setSearchTerm] = useState(filter)
43|
44|  const filteredApps = apps
45|    .sort((a, b) =>
46|      sort === 'asc'
47|        ? a.name.localeCompare(b.name)
48|        : b.name.localeCompare(a.name)
49|    )
50|    .filter((app) =>
51|      appType === 'connected'
52|        ? app.connected
53|        : appType === 'notConnected'
54|          ? !app.connected
55|          : true
56|    )
57|    .filter((app) => app.name.toLowerCase().includes(searchTerm.toLowerCase()))
58|
59|  const handleSearch = (e: ChangeEvent<HTMLInputElement>) => {
60|    setSearchTerm(e.target.value)
61|    navigate({
62|      search: (prev) => ({
63|        ...prev,
64|        filter: e.target.value || undefined,
65|      }),
66|    })
67|  }
68|
69|  const handleTypeChange = (value: AppType) => {
70|    setAppType(value)
71|    navigate({
72|      search: (prev) => ({
73|        ...prev,
74|        type: value === 'all' ? undefined : value,
75|      }),
76|    })
77|  }
78|
79|  const handleSortChange = (sort: 'asc' | 'desc') => {
80|    setSort(sort)
81|    navigate({ search: (prev) => ({ ...prev, sort }) })
82|  }
83|
84|  return (
85|    <>
86|      {/* ===== Top Heading ===== */}
87|      <Header>
88|        <Search className='me-auto' />
89|90|91|92|      </Header>
93|
94|      {/* ===== Content ===== */}
95|      <Main fixed>
96|        <div>
97|          <h1 className='text-2xl font-bold tracking-tight'>
98|            App Integrations
99|          </h1>
100|          <p className='text-muted-foreground'>
101|            Here&apos;s a list of your apps for the integration!
102|          </p>
103|        </div>
104|        <div className='my-4 flex items-end justify-between sm:my-0 sm:items-center'>
105|          <div className='flex flex-col gap-4 sm:my-4 sm:flex-row'>
106|            <Input
107|              placeholder='Filter apps...'
108|              className='h-9 w-40 lg:w-62.5'
109|              value={searchTerm}
110|              onChange={handleSearch}
111|            />
112|            <Select value={appType} onValueChange={handleTypeChange}>
113|              <SelectTrigger className='w-36'>
114|                <SelectValue>{appText.get(appType)}</SelectValue>
115|              </SelectTrigger>
116|              <SelectContent>
117|                <SelectItem value='all'>All Apps</SelectItem>
118|                <SelectItem value='connected'>Connected</SelectItem>
119|                <SelectItem value='notConnected'>Not Connected</SelectItem>
120|              </SelectContent>
121|            </Select>
122|          </div>
123|
124|          <Select value={sort} onValueChange={handleSortChange}>
125|            <SelectTrigger className='w-16'>
126|              <SelectValue>
127|                <SlidersHorizontal size={18} />
128|              </SelectValue>
129|            </SelectTrigger>
130|            <SelectContent align='end'>
131|              <SelectItem value='asc'>
132|                <div className='flex items-center gap-4'>
133|                  <ArrowUpAZ size={16} />
134|                  <span>Ascending</span>
135|                </div>
136|              </SelectItem>
137|              <SelectItem value='desc'>
138|                <div className='flex items-center gap-4'>
139|                  <ArrowDownAZ size={16} />
140|                  <span>Descending</span>
141|                </div>
142|              </SelectItem>
143|            </SelectContent>
144|          </Select>
145|        </div>
146|        <Separator className='shadow-sm' />
147|        <ul className='faded-bottom no-scrollbar grid gap-4 overflow-auto pt-4 pb-16 md:grid-cols-2 lg:grid-cols-3'>
148|          {filteredApps.map((app) => (
149|            <li
150|              key={app.name}
151|              className='rounded-lg border p-4 hover:shadow-md'
152|            >
153|              <div className='mb-8 flex items-center justify-between'>
154|                <div
155|                  className={`flex size-10 items-center justify-center rounded-lg bg-muted p-2`}
156|                >
157|                  {app.logo}
158|                </div>
159|                <Button
160|                  variant='outline'
161|                  size='sm'
162|                  className={`${app.connected ? 'border border-blue-300 bg-blue-50 hover:bg-blue-100 dark:border-blue-700 dark:bg-blue-950 dark:hover:bg-blue-900' : ''}`}
163|                >
164|                  {app.connected ? 'Connected' : 'Connect'}
165|                </Button>
166|              </div>
167|              <div>
168|                <h2 className='mb-1 font-semibold'>{app.name}</h2>
169|                <p className='line-clamp-2 text-gray-500'>{app.desc}</p>
170|              </div>
171|            </li>
172|          ))}
173|        </ul>
174|      </Main>
175|    </>
176|  )
177|}
178|
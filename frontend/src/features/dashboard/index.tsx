1|import { Button } from '@/components/ui/button'
2|import {
3|  Card,
4|  CardContent,
5|  CardDescription,
6|  CardHeader,
7|  CardTitle,
8|} from '@/components/ui/card'
9|import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
11|import { Header } from '@/components/layout/header'
12|import { Main } from '@/components/layout/main'
13|import { TopNav } from '@/components/layout/top-nav'
15|import { Search } from '@/components/search'
17|import { Analytics } from './components/analytics'
18|import { Overview } from './components/overview'
19|import { RecentSales } from './components/recent-sales'
20|
21|export function Dashboard() {
22|  return (
23|    <>
24|      {/* ===== Top Heading ===== */}
25|      <Header>
26|        <TopNav links={topNav} className='me-auto' />
27|        <Search />
28|29|30|31|      </Header>
32|
33|      {/* ===== Main ===== */}
34|      <Main>
35|        <div className='mb-2 flex items-center justify-between space-y-2'>
36|          <h1 className='text-2xl font-bold tracking-tight'>Dashboard</h1>
37|          <div className='flex items-center space-x-2'>
38|            <Button>Download</Button>
39|          </div>
40|        </div>
41|        <Tabs
42|          orientation='vertical'
43|          defaultValue='overview'
44|          className='space-y-4'
45|        >
46|          <div className='w-full overflow-x-auto pb-2'>
47|            <TabsList>
48|              <TabsTrigger value='overview'>Overview</TabsTrigger>
49|              <TabsTrigger value='analytics'>Analytics</TabsTrigger>
50|              <TabsTrigger value='reports' disabled>
51|                Reports
52|              </TabsTrigger>
53|              <TabsTrigger value='notifications' disabled>
54|                Notifications
55|              </TabsTrigger>
56|            </TabsList>
57|          </div>
58|          <TabsContent value='overview' className='space-y-4'>
59|            <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
60|              <Card>
61|                <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-2'>
62|                  <CardTitle className='text-sm font-medium'>
63|                    Total Revenue
64|                  </CardTitle>
65|                  <svg
66|                    xmlns='http://www.w3.org/2000/svg'
67|                    viewBox='0 0 24 24'
68|                    fill='none'
69|                    stroke='currentColor'
70|                    strokeLinecap='round'
71|                    strokeLinejoin='round'
72|                    strokeWidth='2'
73|                    className='h-4 w-4 text-muted-foreground'
74|                  >
75|                    <path d='M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6' />
76|                  </svg>
77|                </CardHeader>
78|                <CardContent>
79|                  <div className='text-2xl font-bold'>$45,231.89</div>
80|                  <p className='text-xs text-muted-foreground'>
81|                    +20.1% from last month
82|                  </p>
83|                </CardContent>
84|              </Card>
85|              <Card>
86|                <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-2'>
87|                  <CardTitle className='text-sm font-medium'>
88|                    Subscriptions
89|                  </CardTitle>
90|                  <svg
91|                    xmlns='http://www.w3.org/2000/svg'
92|                    viewBox='0 0 24 24'
93|                    fill='none'
94|                    stroke='currentColor'
95|                    strokeLinecap='round'
96|                    strokeLinejoin='round'
97|                    strokeWidth='2'
98|                    className='h-4 w-4 text-muted-foreground'
99|                  >
100|                    <path d='M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2' />
101|                    <circle cx='9' cy='7' r='4' />
102|                    <path d='M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75' />
103|                  </svg>
104|                </CardHeader>
105|                <CardContent>
106|                  <div className='text-2xl font-bold'>+2350</div>
107|                  <p className='text-xs text-muted-foreground'>
108|                    +180.1% from last month
109|                  </p>
110|                </CardContent>
111|              </Card>
112|              <Card>
113|                <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-2'>
114|                  <CardTitle className='text-sm font-medium'>Sales</CardTitle>
115|                  <svg
116|                    xmlns='http://www.w3.org/2000/svg'
117|                    viewBox='0 0 24 24'
118|                    fill='none'
119|                    stroke='currentColor'
120|                    strokeLinecap='round'
121|                    strokeLinejoin='round'
122|                    strokeWidth='2'
123|                    className='h-4 w-4 text-muted-foreground'
124|                  >
125|                    <rect width='20' height='14' x='2' y='5' rx='2' />
126|                    <path d='M2 10h20' />
127|                  </svg>
128|                </CardHeader>
129|                <CardContent>
130|                  <div className='text-2xl font-bold'>+12,234</div>
131|                  <p className='text-xs text-muted-foreground'>
132|                    +19% from last month
133|                  </p>
134|                </CardContent>
135|              </Card>
136|              <Card>
137|                <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-2'>
138|                  <CardTitle className='text-sm font-medium'>
139|                    Active Now
140|                  </CardTitle>
141|                  <svg
142|                    xmlns='http://www.w3.org/2000/svg'
143|                    viewBox='0 0 24 24'
144|                    fill='none'
145|                    stroke='currentColor'
146|                    strokeLinecap='round'
147|                    strokeLinejoin='round'
148|                    strokeWidth='2'
149|                    className='h-4 w-4 text-muted-foreground'
150|                  >
151|                    <path d='M22 12h-4l-3 9L9 3l-3 9H2' />
152|                  </svg>
153|                </CardHeader>
154|                <CardContent>
155|                  <div className='text-2xl font-bold'>+573</div>
156|                  <p className='text-xs text-muted-foreground'>
157|                    +201 since last hour
158|                  </p>
159|                </CardContent>
160|              </Card>
161|            </div>
162|            <div className='grid grid-cols-1 gap-4 lg:grid-cols-7'>
163|              <Card className='col-span-1 lg:col-span-4'>
164|                <CardHeader>
165|                  <CardTitle>Overview</CardTitle>
166|                </CardHeader>
167|                <CardContent className='ps-2'>
168|                  <Overview />
169|                </CardContent>
170|              </Card>
171|              <Card className='col-span-1 lg:col-span-3'>
172|                <CardHeader>
173|                  <CardTitle>Recent Sales</CardTitle>
174|                  <CardDescription>
175|                    You made 265 sales this month.
176|                  </CardDescription>
177|                </CardHeader>
178|                <CardContent>
179|                  <RecentSales />
180|                </CardContent>
181|              </Card>
182|            </div>
183|          </TabsContent>
184|          <TabsContent value='analytics' className='space-y-4'>
185|            <Analytics />
186|          </TabsContent>
187|        </Tabs>
188|      </Main>
189|    </>
190|  )
191|}
192|
193|const topNav = [
194|  {
195|    title: 'Overview',
196|    href: 'dashboard/overview',
197|    isActive: true,
198|    disabled: false,
199|  },
200|  {
201|    title: 'Customers',
202|    href: 'dashboard/customers',
203|    isActive: false,
204|    disabled: true,
205|  },
206|  {
207|    title: 'Products',
208|    href: 'dashboard/products',
209|    isActive: false,
210|    disabled: true,
211|  },
212|  {
213|    title: 'Settings',
214|    href: 'dashboard/settings',
215|    isActive: false,
216|    disabled: true,
217|  },
218|]
219|
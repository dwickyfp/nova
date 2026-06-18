const dataNodes = [
  [116, 454],
  [181, 409],
  [251, 468],
  [326, 416],
  [400, 463],
  [475, 411],
  [544, 456],
] as const

export function SignInVisual() {
  return (
    <aside
      className='relative hidden h-svh overflow-hidden border-s border-white/8 bg-[#0b0f14] lg:block'
      aria-label='Nova data platform illustration'
    >
      <div className='absolute inset-0 bg-[radial-gradient(circle_at_50%_42%,rgba(208,71,56,0.16),transparent_34%),linear-gradient(145deg,#11161d_0%,#0b0f14_58%,#090c10_100%)]' />
      <div className='absolute inset-0 opacity-40 [background-image:linear-gradient(rgba(255,255,255,0.025)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.025)_1px,transparent_1px)] [background-size:48px_48px]' />

      <svg
        viewBox='0 0 660 720'
        className='absolute inset-0 size-full'
        role='img'
        aria-labelledby='nova-visual-title nova-visual-description'
        preserveAspectRatio='xMidYMid slice'
      >
        <title id='nova-visual-title'>Nova data constellation</title>
        <desc id='nova-visual-description'>
          Data streams rise from a warehouse grid and form an abstract phoenix.
        </desc>

        <defs>
          <linearGradient id='wing-left' x1='118' y1='394' x2='320' y2='195'>
            <stop stopColor='#D04738' stopOpacity='0.15' />
            <stop offset='0.55' stopColor='#D04738' stopOpacity='0.8' />
            <stop offset='1' stopColor='#F36B5B' />
          </linearGradient>
          <linearGradient id='wing-right' x1='542' y1='394' x2='340' y2='195'>
            <stop stopColor='#D04738' stopOpacity='0.15' />
            <stop offset='0.55' stopColor='#D04738' stopOpacity='0.8' />
            <stop offset='1' stopColor='#F36B5B' />
          </linearGradient>
          <linearGradient id='data-flow' x1='330' y1='590' x2='330' y2='292'>
            <stop stopColor='#D04738' stopOpacity='0' />
            <stop offset='0.46' stopColor='#D04738' stopOpacity='0.6' />
            <stop offset='1' stopColor='#F59E66' />
          </linearGradient>
          <radialGradient id='core-glow'>
            <stop stopColor='#FFFFFF' />
            <stop offset='0.22' stopColor='#F59E66' />
            <stop offset='0.56' stopColor='#F36B5B' stopOpacity='0.55' />
            <stop offset='1' stopColor='#D04738' stopOpacity='0' />
          </radialGradient>
          <filter id='soft-glow' x='-100%' y='-100%' width='300%' height='300%'>
            <feGaussianBlur stdDeviation='9' />
          </filter>
          <filter id='line-glow' x='-30%' y='-30%' width='160%' height='160%'>
            <feGaussianBlur stdDeviation='2.5' result='blur' />
            <feMerge>
              <feMergeNode in='blur' />
              <feMergeNode in='SourceGraphic' />
            </feMerge>
          </filter>
          <clipPath id='visual-frame'>
            <rect width='660' height='720' rx='0' />
          </clipPath>
        </defs>

        <g clipPath='url(#visual-frame)'>
          <g opacity='0.42'>
            {Array.from({ length: 9 }, (_, index) => (
              <path
                key={`grid-ray-${index}`}
                d={`M330 438 L${-90 + index * 105} 720`}
                stroke='#8B98A9'
                strokeOpacity='0.18'
              />
            ))}
            {Array.from({ length: 8 }, (_, index) => {
              const y = 465 + index * 36
              const spread = 62 + index * 56
              return (
                <path
                  key={`grid-row-${index}`}
                  d={`M${330 - spread} ${y} H${330 + spread}`}
                  stroke='#8B98A9'
                  strokeOpacity={0.2 - index * 0.015}
                />
              )
            })}
          </g>

          <g className='nova-data-plane'>
            <path
              d='M96 490 330 425 564 490 330 579Z'
              fill='#171D26'
              fillOpacity='0.82'
              stroke='#344151'
            />
            <path
              d='M141 491 330 441 519 491 330 558Z'
              fill='#11161D'
              stroke='#D04738'
              strokeOpacity='0.28'
            />
            <path
              d='m185 492 145-36 145 36-145 49Z'
              fill='#0B0F14'
              stroke='#F36B5B'
              strokeOpacity='0.22'
            />
          </g>

          <g opacity='0.76'>
            {dataNodes.map(([x, y], index) => (
              <g key={`${x}-${y}`}>
                <circle
                  cx={x}
                  cy={y}
                  r='4'
                  fill={index % 2 ? '#F36B5B' : '#8B98A9'}
                  className='nova-data-node'
                  style={{ animationDelay: `${index * 260}ms` }}
                />
                <circle
                  cx={x}
                  cy={y}
                  r='10'
                  fill='none'
                  stroke='#F36B5B'
                  strokeOpacity='0.12'
                />
              </g>
            ))}
            <path
              d='M116 454 181 409 251 468 326 416 400 463 475 411 544 456'
              fill='none'
              stroke='#8B98A9'
              strokeOpacity='0.34'
              strokeDasharray='3 7'
            />
          </g>

          <g
            fill='none'
            strokeLinecap='round'
            strokeLinejoin='round'
            filter='url(#line-glow)'
          >
            <path
              d='M321 327C279 309 248 273 224 224c-9 42 3 78 37 108-39-17-72-43-100-80 8 51 40 91 99 119-50-8-94-29-131-63 25 56 76 91 153 101'
              stroke='url(#wing-left)'
              strokeWidth='5'
              className='nova-wing nova-wing-left'
            />
            <path
              d='M339 327c42-18 73-54 97-103 9 42-3 78-37 108 39-17 72-43 100-80-8 51-40 91-99 119 50-8 94-29 131-63-25 56-76 91-153 101'
              stroke='url(#wing-right)'
              strokeWidth='5'
              className='nova-wing nova-wing-right'
            />
            <path
              d='M315 340c-31-20-53-44-69-72 1 35 19 65 55 91'
              stroke='#F36B5B'
              strokeOpacity='0.62'
              strokeWidth='2'
            />
            <path
              d='M345 340c31-20 53-44 69-72-1 35-19 65-55 91'
              stroke='#F36B5B'
              strokeOpacity='0.62'
              strokeWidth='2'
            />
          </g>

          <g>
            <path
              d='M330 220c-9 19-8 37 3 54-17 15-22 35-14 59l11 30 11-30c8-24 3-44-14-59 11-17 12-35 3-54Z'
              fill='#D04738'
            />
            <path
              d='M330 345c-14 39-12 90 0 153 12-63 14-114 0-153Z'
              fill='url(#data-flow)'
              opacity='0.72'
              className='nova-tail'
            />
            <path
              d='M314 374c-20 47-26 93-18 139 9-44 21-80 36-108M346 374c20 47 26 93 18 139-9-44-21-80-36-108'
              fill='none'
              stroke='#D04738'
              strokeOpacity='0.4'
              strokeWidth='2'
            />
          </g>

          <g className='nova-core'>
            <circle
              cx='330'
              cy='327'
              r='55'
              fill='url(#core-glow)'
              opacity='0.48'
              filter='url(#soft-glow)'
            />
            <path
              d='m330 301 6 20 20 6-20 6-6 20-6-20-20-6 20-6 6-20Z'
              fill='#F59E66'
            />
            <path
              d='m330 313 3 11 11 3-11 3-3 11-3-11-11-3 11-3 3-11Z'
              fill='#FFFFFF'
            />
          </g>

          <g opacity='0.52'>
            <rect
              x='196'
              y='532'
              width='50'
              height='34'
              rx='4'
              fill='#202833'
            />
            <rect
              x='255'
              y='517'
              width='57'
              height='49'
              rx='4'
              fill='#202833'
            />
            <rect
              x='321'
              y='527'
              width='49'
              height='39'
              rx='4'
              fill='#202833'
            />
            <rect
              x='379'
              y='511'
              width='58'
              height='55'
              rx='4'
              fill='#202833'
            />
            <rect
              x='446'
              y='535'
              width='39'
              height='31'
              rx='4'
              fill='#202833'
            />
          </g>
        </g>
      </svg>

      <div className='absolute inset-x-0 bottom-0 bg-gradient-to-t from-[#0b0f14] via-[#0b0f14]/92 to-transparent px-12 pt-28 pb-12'>
        <div className='max-w-md'>
          <div className='mb-4 flex items-center gap-2 text-xs font-semibold tracking-[0.2em] text-[#f36b5b] uppercase'>
            <span className='size-1.5 rounded-full bg-[#f36b5b] shadow-[0_0_12px_#f36b5b]' />
            Data warehouse + AI
          </div>
          <p className='text-2xl font-semibold tracking-tight text-white'>
            From raw data to intelligent action.
          </p>
          <p className='mt-2 max-w-sm text-sm leading-6 text-[#9ca8b8]'>
            Query, govern, and build AI experiences on the speed of StarRocks.
          </p>
        </div>
      </div>
    </aside>
  )
}

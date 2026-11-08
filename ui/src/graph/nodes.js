// The seven thoughts of the findings, in the order the work goes: what the brief says, who to ask, what to ask, what came back,
// how it compares, the plan, and where everything stands. Circle positions are on the 360 x 340 drawing the graph is drawn in.
export const NODES = [
  { id: 'brief', label: 'Your brief', at: [108, 58, 48], mark: 'M8 5h16v23H8z M12 11h8 M12 16h8 M12 21h5' },
  { id: 'vendors', label: 'Find vendors', at: [225, 69, 61], mark: 'M7 26V12l9-7 9 7v14z M12 26v-9h8v9 M4 12l12-9 12 9' },
  { id: 'message', label: 'The message', at: [309, 172, 45], mark: 'M4 8h24v18H4z M4 9l12 10L28 9' },
  { id: 'replies', label: 'Replies', at: [235, 252, 52], mark: 'M5 5h22v17H15l-7 6v-6H5z M10 11h12 M10 16h8' },
  { id: 'compare', label: 'Compare', at: [116, 267, 62], mark: 'M5 7h8v20H5z M19 3h8v24h-8z M3 29h26' },
  { id: 'plan', label: 'The plan', at: [45, 165, 39], mark: 'M7 7h18v22H7z M11 3v8 M21 3v8 M7 14h18 M12 20l3 3 6-6' },
  { id: 'overview', label: 'Overview', at: [162, 161, 48], mark: 'M16 3l3 9 10 4-10 3-3 10-3-10-10-3 10-4z' },
];

const LINKS = [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [0, 6]];

export const wires = LINKS.map(([a, b]) => [NODES[a].at, NODES[b].at]);

export const TITLES = {
  brief: ['YOUR BRIEF', ['The idea.', 'The human decisions.'], 'What you said, and what was read from it. Nothing here is planned from until you say so.'],
  vendors: ['WHO TO ASK', ['Find your', 'kind of people.'], 'Search for businesses, or add the ones you already know. Prices come from them, not from us.'],
  message: ['THE ASK', ['A thoughtful ask.', 'An exact decision.'], 'One message per business. You read the exact words before anything leaves.'],
  replies: ['THE REPLIES', ['Read the lines.', 'Keep the doubt.'], 'Nothing a vendor writes counts until you confirm that line.'],
  compare: ['SIDE BY SIDE', ['The price.', 'The whole picture.'], 'Only quotes you have confirmed are ranked, by a rule that does not care who answered first.'],
  plan: ['THE GOOD PART', ['A gathering.', 'Loose ends included.'], 'A plan built from the vendors who answered, checked before you see it.'],
  overview: ['WHERE THINGS STAND', ['What is done.', 'What is waiting.'], 'Every step, and who it is waiting on.'],
};

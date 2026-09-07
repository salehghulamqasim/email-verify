export default function DataSection() {
  return (
    <div className="max-w-4xl mx-auto p-6">
      {/* Local Header / "Bottom of Top" */}
      <div className="flex items-center justify-between border-b border-gray-200 pb-4 mb-6">
        <h2 className="text-xl font-semibold text-gray-900">Active Tournament Data</h2>
        
        {/* Action Group */}
        <div className="flex items-center gap-2">
          {/* Unobtrusive Reset Button (Ghost Style) */}
          <button 
            onClick={() => {/* handle reset */}}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-500 rounded-md hover:text-gray-900 hover:bg-gray-100 transition-colors focus:outline-none focus:ring-2 focus:ring-gray-200"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Reset
          </button>
          
          {/* Primary Action for contrast */}
          <button className="px-4 py-1.5 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 transition-colors">
            Save Changes
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="bg-white rounded-lg shadow p-6 text-gray-500 text-center">
        Content goes here...
      </div>
    </div>
  );
}

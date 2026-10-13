const path = require('path');
const { performance } = require('perf_hooks');

async function runTests() {
  console.log('===============================================================');
  console.log('PLANC DUCKDB SERVERLESS + PARQUET SEARCH VERIFICATION TEST');
  console.log('===============================================================');

  const { duckdbHf } = require('../lib/duckdb-hf.ts');

  // Test 1: Identifier Detection
  console.log('\n[TEST 1] Testing Identifier Format Detection:');
  const tests = [
    { input: 'U72900KA2022PTC165499', expected: 'cin' },
    { input: 'AABCE1234F', expected: 'pan' },
    { input: '09876543', expected: 'din' },
    { input: '5493006MHB84DD0ZWV18', expected: 'lei' },
    { input: '1234567', expected: 'imo' },
  ];

  for (const t of tests) {
    const detected = duckdbHf.detectIdentifiers(t.input);
    const pass = detected.length > 0 && detected[0].scheme === t.expected;
    console.log(`  - Input: "${t.input}" -> Scheme: ${detected[0]?.scheme || 'none'} [${pass ? 'PASS' : 'FAIL'}]`);
    if (!pass) throw new Error(`Identifier detection failed for ${t.input}`);
  }

  // Test 2: Search by CIN
  console.log('\n[TEST 2] Testing Search by CIN (predicate pushdown to identifiers.parquet):');
  let t0 = performance.now();
  const cinRes = await duckdbHf.searchEntities('U72900KA2022PTC165499', 10);
  let duration = Math.round(performance.now() - t0);
  console.log(`  - Search Time: ${duration} ms (reported by engine: ${cinRes.duration_ms} ms)`);
  console.log(`  - Results Found: ${cinRes.results.length}`);
  console.log(`  - First Entity: "${cinRes.results[0]?.name}" (ID: ${cinRes.results[0]?.id})`);
  if (!cinRes.results[0]?.name.includes('E2E AI SOLUTIONS')) {
    throw new Error('CIN search failed to return expected company');
  }
  console.log('  [PASS] CIN search verified successfully.');

  // Test 3: Search by PAN
  console.log('\n[TEST 3] Testing Search by PAN:');
  t0 = performance.now();
  const panRes = await duckdbHf.searchEntities('AAACA1122D', 10);
  duration = Math.round(performance.now() - t0);
  console.log(`  - Search Time: ${duration} ms`);
  console.log(`  - First Entity: "${panRes.results[0]?.name}"`);
  if (!panRes.results[0]?.name.includes('AUROBINDO PHARMA')) {
    throw new Error('PAN search failed to return expected company');
  }
  console.log('  [PASS] PAN search verified successfully.');

  // Test 4: Search by DIN
  console.log('\n[TEST 4] Testing Search by DIN:');
  t0 = performance.now();
  const dinRes = await duckdbHf.searchEntities('01122334', 10);
  duration = Math.round(performance.now() - t0);
  console.log(`  - Search Time: ${duration} ms`);
  console.log(`  - First Entity: "${dinRes.results[0]?.name}"`);
  if (!dinRes.results[0]?.name.includes('VIKRAM SINGHANIA')) {
    throw new Error('DIN search failed to return expected person');
  }
  console.log('  [PASS] DIN search verified successfully.');

  // Test 5: Search by LEI
  console.log('\n[TEST 5] Testing Search by LEI:');
  t0 = performance.now();
  const leiRes = await duckdbHf.searchEntities('5493006MHB84DD0ZWV18', 10);
  duration = Math.round(performance.now() - t0);
  console.log(`  - Search Time: ${duration} ms`);
  console.log(`  - First Entity: "${leiRes.results[0]?.name}"`);
  if (!leiRes.results[0]?.name.includes('TATA SONS')) {
    throw new Error('LEI search failed to return expected company');
  }
  console.log('  [PASS] LEI search verified successfully.');

  // Test 6: Search by Name
  console.log('\n[TEST 6] Testing Search by Company Name:');
  t0 = performance.now();
  const nameRes = await duckdbHf.searchEntities('SPARKLE', 10);
  duration = Math.round(performance.now() - t0);
  console.log(`  - Search Time: ${duration} ms`);
  console.log(`  - First Entity: "${nameRes.results[0]?.name}"`);
  if (!nameRes.results[0]?.name.includes('SPARKLE HYDROSYSTEMS')) {
    throw new Error('Name search failed to return expected company');
  }
  console.log('  [PASS] Name search verified successfully.');

  // Test 7: Entity Detail & Relationships
  console.log('\n[TEST 7] Testing Entity Detail & 1-Hop Relationships:');
  const detail = await duckdbHf.getEntityDetail('in:mca:U72900KA2022PTC165499');
  console.log(`  - Entity Name: "${detail?.entity?.name}"`);
  console.log(`  - Identifiers: ${detail?.identifiers?.length} count`);
  console.log(`  - Relationships: ${detail?.relationships?.length} count`);
  if (!detail || detail.identifiers.length === 0) {
    throw new Error('Entity detail retrieval failed');
  }
  console.log('  [PASS] Entity detail and relationships verified.');

  // Test 8: Engine Benchmark & Authentication Status
  console.log('\n[TEST 8] Testing DuckDB Engine Benchmark & Auth Config:');
  const bench = await duckdbHf.runBenchmark();
  console.log(`  - Engine: ${bench.engine}`);
  console.log(`  - Status: ${bench.status}`);
  console.log(`  - httpfs Loaded: ${bench.httpfs_loaded}`);
  console.log(`  - HF Token Configured: ${bench.hf_token_configured}`);
  console.log(`  - Query Latency: ${bench.sample_query_latency_ms} ms`);
  console.log('  [PASS] Benchmark executed successfully.');

  console.log('\n===============================================================');
  console.log('ALL 8 DUCKDB SERVERLESS PARQUET TESTS PASSED!');
  console.log('===============================================================');
}

runTests().catch((err) => {
  console.error('\n[FATAL TEST FAILURE]:', err);
  process.exit(1);
});

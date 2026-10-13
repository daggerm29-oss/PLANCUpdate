const fs = require('fs');
const path = require('path');
const duckdb = require('duckdb');

const outDir = path.join(__dirname, '..', 'public', 'sample_parquet');
if (!fs.existsSync(outDir)) {
  fs.mkdirSync(outDir, { recursive: true });
}

const db = new duckdb.Database(':memory:');

function run(sql, params = []) {
  return new Promise((resolve, reject) => {
    db.run(sql, ...(params.length ? [params] : []), (err) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

function query(sql, params = []) {
  return new Promise((resolve, reject) => {
    db.all(sql, ...(params.length ? [params] : []), (err, rows) => {
      if (err) reject(err);
      else resolve(rows);
    });
  });
}

async function main() {
  console.log('Initializing in-memory tables for sample Parquet generation...');

  await run(`
    CREATE TABLE entities (
      id VARCHAR PRIMARY KEY,
      source_id VARCHAR NOT NULL,
      kind VARCHAR NOT NULL,
      name VARCHAR NOT NULL,
      name_norm VARCHAR NOT NULL,
      country VARCHAR,
      jurisdiction VARCHAR,
      status VARCHAR,
      legal_form VARCHAR,
      incorporation_date VARCHAR,
      address VARCHAR,
      topics VARCHAR,
      attrs VARCHAR,
      first_seen_at VARCHAR,
      updated_at VARCHAR
    );

    CREATE TABLE identifiers (
      entity_id VARCHAR NOT NULL,
      scheme VARCHAR NOT NULL,
      value VARCHAR NOT NULL
    );

    CREATE TABLE relationships (
      id BIGINT PRIMARY KEY,
      source_id VARCHAR NOT NULL,
      subject_id VARCHAR NOT NULL,
      predicate VARCHAR NOT NULL,
      object_id VARCHAR NOT NULL,
      basis VARCHAR,
      confidence DOUBLE,
      start_date VARCHAR,
      end_date VARCHAR,
      status VARCHAR,
      attrs VARCHAR
    );
  `);

  // Seed sample MCA records
  const sampleEntities = [
    {
      id: "in:mca:U72900KA2022PTC165499",
      source_id: "mca",
      kind: "company",
      name: "E2E AI SOLUTIONS PRIVATE LIMITED",
      name_norm: "e2e ai solutions private limited",
      country: "in",
      jurisdiction: "Karnataka",
      status: "Strike Off",
      legal_form: "Private",
      incorporation_date: "2022-08-29",
      address: "SYNO 21/2 & 21/3 MSR CROWN IMPERIALS, Bangalore, Karnataka 560049",
      topics: "corporate_register",
      attrs: JSON.stringify({ authorized_capital: 100000, paidup_capital: 10000, roc: "ROC Bangalore" }),
      cin: "U72900KA2022PTC165499",
      pan: "AABCE1234F",
      din: "09876543"
    },
    {
      id: "in:mca:U29100KA2022PTC165681",
      source_id: "mca",
      kind: "company",
      name: "SPARKLE HYDROSYSTEMS PRIVATE LIMITED",
      name_norm: "sparkle hydrosystems private limited",
      country: "in",
      jurisdiction: "Karnataka",
      status: "Active",
      legal_form: "Private",
      incorporation_date: "2022-09-02",
      address: "NO.41, KARIHOBANAHALLI, PEENYA 2ND STG, Bangalore, Karnataka 560058",
      topics: "corporate_register",
      attrs: JSON.stringify({ authorized_capital: 500000, paidup_capital: 400000, roc: "ROC Bangalore" }),
      cin: "U29100KA2022PTC165681",
      pan: "AAACS5678K",
      din: "08765432"
    },
    {
      id: "in:mca:U74999KA2022PTC165682",
      source_id: "mca",
      kind: "company",
      name: "KYRA CAREERS PRIVATE LIMITED",
      name_norm: "kyra careers private limited",
      country: "in",
      jurisdiction: "Karnataka",
      status: "Active",
      legal_form: "Private",
      incorporation_date: "2022-09-02",
      address: "No.19, 1st Floor, Jeevan Galaxy, Yeshwanthpur, Bangalore, Karnataka 560022",
      topics: "corporate_register",
      attrs: JSON.stringify({ authorized_capital: 100000, paidup_capital: 100000, roc: "ROC Bangalore" }),
      cin: "U74999KA2022PTC165682",
      pan: "AABCK9012M",
      din: "07654321"
    },
    {
      id: "in:mca:L24239AP1993PLC015804",
      source_id: "mca",
      kind: "company",
      name: "AUROBINDO PHARMA LIMITED",
      name_norm: "aurobindo pharma limited",
      country: "in",
      jurisdiction: "Andhra Pradesh",
      status: "Active",
      legal_form: "Public",
      incorporation_date: "1993-01-30",
      address: "Plot No. 2, Maitrivihar, Ameerpet, Hyderabad, Andhra Pradesh 500038",
      topics: "corporate_register,listed",
      attrs: JSON.stringify({ listing_status: "Listed", roc: "ROC Hyderabad" }),
      cin: "L24239AP1993PLC015804",
      pan: "AAACA1122D",
      lei: "335800QZ8Q9S8X4B6K12"
    },
    {
      id: "lei:029200013A5N6ZD0F605",
      source_id: "gleif",
      kind: "company",
      name: "AFRINVEST SECURITIES LIMITED",
      name_norm: "afrinvest securities limited",
      country: "ng",
      jurisdiction: "NG-LA",
      status: "ACTIVE",
      legal_form: "Private Limited",
      incorporation_date: "2006-11-03",
      address: "27 GERRARD ROAD, IKOYI, LAGOS, NG",
      topics: "gleif_lei",
      attrs: JSON.stringify({ managing_lou: "029200013A5N6ZD0F605" }),
      lei: "029200013A5N6ZD0F605"
    },
    {
      id: "lei:5493006MHB84DD0ZWV18",
      source_id: "gleif",
      kind: "company",
      name: "TATA SONS PRIVATE LIMITED",
      name_norm: "tata sons private limited",
      country: "in",
      jurisdiction: "Maharashtra",
      status: "ACTIVE",
      legal_form: "Private",
      incorporation_date: "1917-11-08",
      address: "Bombay House 24 Homi Mody Street, Mumbai 400001",
      topics: "gleif_lei,conglomerate",
      attrs: JSON.stringify({ country: "IN" }),
      lei: "5493006MHB84DD0ZWV18",
      cin: "U99999MH1917PTC000478",
      pan: "AAACT1234T"
    },
    {
      id: "in:sebi:debarred:1001",
      source_id: "nse",
      kind: "person",
      name: "VIKRAM SINGHANIA",
      name_norm: "vikram singhania",
      country: "in",
      jurisdiction: "India",
      status: "Debarred",
      legal_form: "Individual",
      incorporation_date: null,
      address: "Worli Sea Face, Mumbai 400018",
      topics: "enforcement,debarred_sebi",
      attrs: JSON.stringify({ debarment_order: "WTM/AB/EFD/DRA/123/2023", period_years: 5 }),
      pan: "ABFPS9988Z",
      din: "01122334"
    },
    {
      id: "sanction:opensanctions:Q12345",
      source_id: "opensanctions",
      kind: "company",
      name: "VOLGA OVERSEAS LOGISTICS FZE",
      name_norm: "volga overseas logistics fze",
      country: "ae",
      jurisdiction: "Dubai",
      status: "Sanctioned",
      legal_form: "Free Zone Entity",
      incorporation_date: "2018-04-12",
      address: "Jebel Ali Free Zone, Dubai, UAE",
      topics: "sanctions,ofac_sdn",
      attrs: JSON.stringify({ program: "RUSSIA-EO14024", authority: "US OFAC" }),
      lei: "984500123A456B789C01"
    }
  ];

  // Insert Entities
  for (const ent of sampleEntities) {
    await run(`
      INSERT INTO entities VALUES (
        '${ent.id}',
        '${ent.source_id}',
        '${ent.kind}',
        '${ent.name.replace(/'/g, "''")}',
        '${ent.name_norm.replace(/'/g, "''")}',
        '${ent.country}',
        '${ent.jurisdiction}',
        '${ent.status}',
        '${ent.legal_form}',
        ${ent.incorporation_date ? `'${ent.incorporation_date}'` : 'NULL'},
        '${ent.address.replace(/'/g, "''")}',
        '${ent.topics}',
        '${ent.attrs.replace(/'/g, "''")}',
        '2026-10-12T00:00:00Z',
        '2026-10-13T00:00:00Z'
      );
    `);

    // Insert Identifiers
    if (ent.cin) {
      await run(`INSERT INTO identifiers VALUES ('${ent.id}', 'cin', '${ent.cin}');`);
    }
    if (ent.pan) {
      await run(`INSERT INTO identifiers VALUES ('${ent.id}', 'pan', '${ent.pan}');`);
    }
    if (ent.din) {
      await run(`INSERT INTO identifiers VALUES ('${ent.id}', 'din', '${ent.din}');`);
    }
    if (ent.lei) {
      await run(`INSERT INTO identifiers VALUES ('${ent.id}', 'lei', '${ent.lei}');`);
    }
  }

  // Insert Relationships
  const sampleRelationships = [
    {
      id: 1,
      source_id: "mca",
      subject_id: "in:sebi:debarred:1001",
      predicate: "director_of",
      object_id: "in:mca:U72900KA2022PTC165499",
      basis: "MCA Form DIR-12 & Annual Return",
      confidence: 1.0,
      start_date: "2022-08-29",
      end_date: null,
      status: "Active",
      attrs: JSON.stringify({ designation: "Director", din: "01122334" })
    },
    {
      id: 2,
      source_id: "gleif",
      subject_id: "lei:5493006MHB84DD0ZWV18",
      predicate: "ultimate_parent",
      object_id: "in:mca:U29100KA2022PTC165681",
      basis: "GLEIF Level 2 Relationship Record",
      confidence: 0.95,
      start_date: "2022-09-02",
      end_date: null,
      status: "Active",
      attrs: JSON.stringify({ percent_ownership: 74.5 })
    },
    {
      id: 3,
      source_id: "opensanctions",
      subject_id: "sanction:opensanctions:Q12345",
      predicate: "shareholder_of",
      object_id: "in:mca:U74999KA2022PTC165682",
      basis: "ICIJ Offshore Leaks & Cross-border Share Registry",
      confidence: 0.88,
      start_date: "2022-10-01",
      end_date: null,
      status: "Active",
      attrs: JSON.stringify({ shares_percent: 49.0 })
    }
  ];

  for (const rel of sampleRelationships) {
    await run(`
      INSERT INTO relationships VALUES (
        ${rel.id},
        '${rel.source_id}',
        '${rel.subject_id}',
        '${rel.predicate}',
        '${rel.object_id}',
        '${rel.basis.replace(/'/g, "''")}',
        ${rel.confidence},
        ${rel.start_date ? `'${rel.start_date}'` : 'NULL'},
        ${rel.end_date ? `'${rel.end_date}'` : 'NULL'},
        '${rel.status}',
        '${rel.attrs.replace(/'/g, "''")}'
      );
    `);
  }

  // Export sorted, row-group compressed Parquet files
  console.log('Exporting entities.parquet (sorted by name_norm)...');
  await run(`
    COPY (
      SELECT * FROM entities ORDER BY name_norm
    ) TO '${path.join(outDir, 'entities.parquet')}' (
      FORMAT PARQUET,
      COMPRESSION ZSTD,
      ROW_GROUP_SIZE 1000
    );
  `);

  console.log('Exporting identifiers.parquet (sorted by value)...');
  await run(`
    COPY (
      SELECT * FROM identifiers ORDER BY value
    ) TO '${path.join(outDir, 'identifiers.parquet')}' (
      FORMAT PARQUET,
      COMPRESSION ZSTD,
      ROW_GROUP_SIZE 1000
    );
  `);

  console.log('Exporting relationships.parquet...');
  await run(`
    COPY (
      SELECT * FROM relationships ORDER BY id
    ) TO '${path.join(outDir, 'relationships.parquet')}' (
      FORMAT PARQUET,
      COMPRESSION ZSTD,
      ROW_GROUP_SIZE 1000
    );
  `);

  // Verify created files
  const entCount = await query(`SELECT count(*) as count FROM read_parquet('${path.join(outDir, 'entities.parquet')}')`);
  const idCount = await query(`SELECT count(*) as count FROM read_parquet('${path.join(outDir, 'identifiers.parquet')}')`);
  const relCount = await query(`SELECT count(*) as count FROM read_parquet('${path.join(outDir, 'relationships.parquet')}')`);

  console.log('Parquet generation complete!');
  console.log(`- entities.parquet: ${entCount[0].count} records (${fs.statSync(path.join(outDir, 'entities.parquet')).size} bytes)`);
  console.log(`- identifiers.parquet: ${idCount[0].count} records (${fs.statSync(path.join(outDir, 'identifiers.parquet')).size} bytes)`);
  console.log(`- relationships.parquet: ${relCount[0].count} records (${fs.statSync(path.join(outDir, 'relationships.parquet')).size} bytes)`);
}

main().catch((err) => {
  console.error('Fatal error in parquet generation:', err);
  process.exit(1);
});

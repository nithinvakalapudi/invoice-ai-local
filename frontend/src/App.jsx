import React, { useEffect, useMemo, useState } from 'react'
import {
  AlertDialog, AppBar, Badge, Button, Card, Collapsible, Container,
  Dialog, Eyebrow, Field, FileUpload, Grid, Heading, HStack, Input,
  NumberInput, Paragraph, Progress, Section, Stack, Table, Tabs,
  Text, VStack,
} from '@flowstack-ui/brick'

const editableFields = [
  ['invoice_number', 'Invoice number'], ['invoice_date', 'Invoice date'],
  ['vendor_name', 'Vendor'], ['region', 'Region (vendor location)'],
  ['currency', 'Currency'], ['subtotal', 'Subtotal'],
  ['customer_name', 'Billed company'],
  ['tax_amount', 'Tax'], ['discount', 'Discount'], ['shipping_amount', 'Shipping'],
  ['total_amount', 'Total amount'], ['service_start_date', 'Service start date'],
  ['service_end_date', 'Service end date'], ['customer_email', 'Task email'],
  ['vendor_email', 'Email printed on invoice'], ['scope', 'Scope'],
  ['invoice_comments', 'Comments'],
]

const api = async (path, options = {}) => {
  const response = await fetch(`/api${path}`, options)
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      message = typeof body.detail === 'string' ? body.detail : message
    } catch { /* The status text is still actionable. */ }
    throw new Error(message)
  }
  return response.json()
}

const display = (value) => value === null || value === undefined || value === '' ? '—' : String(value)
const statusTone = (status) => ['VALID', 'SUCCESS'].includes(status) ? 'success' : status === 'FAILED' ? 'danger' : 'warning'
const href = (path) => `/api/download${path}`

function DownloadButton({ path, children }) {
  return <Button href={href(path)} variant="outline" size="sm">{children}</Button>
}

function Metric({ label, value, tone = 'neutral' }) {
  return <Card.Root size="sm" variant="outline">
    <Card.Header><Card.Description>{label}</Card.Description></Card.Header>
    <Card.Content><Text as="p" variant="title-lg" tone={tone === 'neutral' ? 'primary' : tone}>{display(value)}</Text></Card.Content>
  </Card.Root>
}

function ReviewDialog({ batch, index, tolerance, threshold, onSaved, onError }) {
  const [open, setOpen] = useState(false)
  const [values, setValues] = useState({})
  const [busy, setBusy] = useState(false)
  const result = batch?.results?.[index]
  if (!result || result.processing_status === 'FAILED') return null
  const launch = () => {
    setValues(Object.fromEntries(editableFields.map(([key]) => [key, result.invoice[key] ?? ''])))
    setOpen(true)
  }
  const save = async () => {
    setBusy(true)
    try {
      const updated = await api(`/batches/${batch.batch_id}/review`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ generation: batch.generation, index, fields: values, tolerance, threshold }),
      })
      onSaved(updated)
      setOpen(false)
    } catch (error) { onError(error.message) }
    finally { setBusy(false) }
  }
  return <Dialog.Root open={open} onOpenChange={setOpen}>
    <Button size="sm" variant="ghost" onPress={launch}>Review details</Button>
    <Dialog.Portal>
      <Dialog.Overlay />
      <Dialog.Content size="lg">
        <Dialog.Header>
          <Dialog.Title>Review invoice</Dialog.Title>
          <Dialog.Description>{result.source_file} · Correct the extracted fields, then revalidate and save.</Dialog.Description>
        </Dialog.Header>
        <Dialog.Body>
          <VStack gap={4}>
            {(result.errors.length || result.warnings.length) > 0 &&
              <Paragraph tone="warning">{[...result.errors, ...result.warnings].join(' · ')}</Paragraph>}
            <Grid.Root columns={{ initial: 1, md: 2 }} gap={3}>
              {editableFields.map(([key, label]) => <Field.Root key={key}>
                <Field.Label>{label}</Field.Label>
                <Input name={key} value={values[key] ?? ''} onChange={(event) => setValues({ ...values, [key]: event.target.value })} />
              </Field.Root>)}
            </Grid.Root>
          </VStack>
        </Dialog.Body>
        <Dialog.Footer>
          <Dialog.Close asChild><Button variant="ghost">Cancel</Button></Dialog.Close>
          <Button tone="accent" loading={busy} onPress={save}>Confirm against source and save</Button>
        </Dialog.Footer>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
}

function Results({ batch, columns, requiredFields, tolerance, threshold, onSaved, onError }) {
  const [query, setQuery] = useState('')
  const results = batch?.results || []
  const shown = useMemo(() => results.map((result, index) => ({ result, index }))
    .filter(({ result }) => !query || JSON.stringify(result).toLowerCase().includes(query.toLowerCase())), [results, query])
  if (!batch) return null
  const counts = batch.counts
  return <Section spacing="md" aria-labelledby="results-heading">
    <Container measure="max">
      <VStack gap={5}>
        <HStack align="center" justify="between" wrap gap={3}>
          <VStack gap={1}><Eyebrow>Extraction output</Eyebrow><Heading id="results-heading" level={2} variant="title-lg">Current batch</Heading></VStack>
          <Badge tone="info">Saved to local history</Badge>
        </HStack>
        <Grid.Root columns={{ initial: 2, md: 4, lg: 8 }} gap={3}>
          <Metric label="Documents" value={counts.total} />
          <Metric label="Processed" value={counts.processed} tone="success" />
          <Metric label="OCR only" value={counts.ocr_only || 0} tone="warning" />
          <Metric label="Auto-approved" value={counts.auto_approved || 0} tone="success" />
          <Metric label="Imported rows" value={counts.imported || 0} tone="warning" />
          <Metric label="Review needed" value={counts.review} tone="warning" />
          <Metric label="Failed" value={counts.failed} tone="danger" />
          <Metric label="Duplicates" value={counts.duplicate} />
        </Grid.Root>
        <Paragraph tone="secondary">Currently required: {requiredFields.join(', ')}. A blank in one of these fields needs review. Other blank columns do not by themselves. Review is also needed for validation errors, possible duplicates, or local extraction that has not been verified.</Paragraph>
        <Card.Root variant="outline">
          <Card.Header><Card.Title as="h3">Invoice results</Card.Title><Card.Description>Each attachment is processed independently. Scroll across to see every Invoice_data.csv field. Extraction completeness is the share of expected fields filled, not proven accuracy. Confirm a draft against its source to clear review when validation passes.</Card.Description></Card.Header>
          <Card.Content>
            <VStack gap={3}>
              <Field.Root>
                <Field.Label>Search results</Field.Label>
                <Input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search invoice, vendor, source or comment" />
              </Field.Root>
              <Table.Container>
                <Table.Root variant="line" size="sm" density="compact" style={{ minInlineSize: '2600px' }}>
                  <Table.Caption>Invoice extraction results</Table.Caption>
                  <Table.Header><Table.Row>
                    {columns.map((name) => <Table.Head key={name} scope="col">{name}</Table.Head>)}
                    <Table.Head scope="col">Review</Table.Head>
                  </Table.Row></Table.Header>
                  <Table.Body>{shown.map(({ result, index }) => <Table.Row key={`${result.source_file}-${index}`}>
                    {columns.map((name) => name === 'Invoice Number'
                      ? <Table.Head key={name} scope="row">{display(result.mdm_row?.[name])}</Table.Head>
                      : name === 'Review status'
                        ? <Table.Cell key={name}><Badge tone={statusTone(result.mdm_row?.[name])}>{display(result.mdm_row?.[name])}</Badge></Table.Cell>
                        : <Table.Cell key={name}>{display(result.mdm_row?.[name])}</Table.Cell>)}
                    <Table.Cell><ReviewDialog batch={batch} index={index} tolerance={tolerance} threshold={threshold} onSaved={onSaved} onError={onError} /></Table.Cell>
                  </Table.Row>)}</Table.Body>
                </Table.Root>
              </Table.Container>
              {!shown.length && <Paragraph tone="muted">No invoices match this search.</Paragraph>}
            </VStack>
          </Card.Content>
          <Card.Footer><HStack wrap gap={2}>
            {['Invoice_Data.csv', 'MDM/Invoice_data.csv', 'Invoice_Line_Items.csv', 'Audit_Report.csv', 'invoice_results.zip'].map((name) =>
              <DownloadButton key={name} path={`/batch/${batch.batch_id}/${name}`}>{name === 'invoice_results.zip' ? 'Download all as ZIP' : name}</DownloadButton>)}
          </HStack></Card.Footer>
        </Card.Root>
      </VStack>
    </Container>
  </Section>
}

function History({ history, onRefresh, onOpen, onError }) {
  const [expanded, setExpanded] = useState(false)
  useEffect(() => { if (expanded && !history) onRefresh() }, [expanded, history, onRefresh])
  return <Collapsible.Root open={expanded} onOpenChange={setExpanded} variant="outline">
    <Collapsible.Trigger>Saved history and cumulative reports <Collapsible.Indicator /></Collapsible.Trigger>
    <Collapsible.Content><Collapsible.ContentInner>
      <VStack gap={4}>
        <Paragraph tone="secondary">Saved invoices remain on this computer. Invoice_data.csv combines every saved batch. Reset clears its invoice rows while keeping the trained model.</Paragraph>
        {history?.warnings?.map((warning, index) => <Paragraph key={index} tone="warning">{warning}</Paragraph>)}
        <HStack wrap gap={2}>
          <Button variant="soft" size="sm" onPress={async () => {
            try { await api('/history/refresh', { method: 'POST' }); onRefresh() }
            catch (error) { onError(error.message) }
          }}>Refresh reports</Button>
          {history?.report_files?.map((name) => <DownloadButton key={name} path={`/history/${name}`}>{name}</DownloadButton>)}
        </HStack>
        {history?.batches?.length ? <Table.Container><Table.Root variant="line" size="sm">
          <Table.Caption>Saved processing batches</Table.Caption>
          <Table.Header><Table.Row><Table.Head>Batch</Table.Head><Table.Head>Documents</Table.Head><Table.Head>Review needed</Table.Head><Table.Head>Source</Table.Head><Table.Head>Action</Table.Head></Table.Row></Table.Header>
          <Table.Body>{history.batches.map((batch) => <Table.Row key={batch.id}>
            <Table.Head scope="row">{batch.id.slice(0, 12)}</Table.Head><Table.Cell>{batch.counts.total}</Table.Cell>
            <Table.Cell>{batch.counts.review}</Table.Cell><Table.Cell>{batch.sources.join(', ')}</Table.Cell>
            <Table.Cell><Button size="sm" variant="ghost" onPress={() => onOpen(batch.id)}>Open batch</Button></Table.Cell>
          </Table.Row>)}</Table.Body></Table.Root></Table.Container> : <Paragraph tone="muted">No batches saved yet.</Paragraph>}
      </VStack>
    </Collapsible.ContentInner></Collapsible.Content>
  </Collapsible.Root>
}

function Benchmarks({ onError }) {
  const [expanded, setExpanded] = useState(false)
  const [runs, setRuns] = useState(null)
  const [chosen, setChosen] = useState('')
  const [details, setDetails] = useState(null)
  const [filter, setFilter] = useState('ALL')
  useEffect(() => {
    if (!expanded || runs) return
    api('/tests').then((data) => { setRuns(data.runs); setChosen(data.runs[0]?.id || '') }).catch((error) => onError(error.message))
  }, [expanded, runs, onError])
  useEffect(() => {
    if (!expanded || !chosen) return
    api(`/tests/${chosen}?limit=500`).then(setDetails).catch((error) => onError(error.message))
  }, [expanded, chosen, onError])
  const rows = (details?.rows || []).filter((row) => filter === 'ALL' || row.validation_status === filter)
  return <Collapsible.Root open={expanded} onOpenChange={setExpanded} variant="outline">
    <Collapsible.Trigger>Public dataset test results <Collapsible.Indicator /></Collapsible.Trigger>
    <Collapsible.Content><Collapsible.ContentInner><VStack gap={4}>
      <Paragraph tone="secondary">Validation status is not a guarantee of extraction accuracy. Reference-field mismatches and review comments appear in the report.</Paragraph>
      {!runs?.length && <Paragraph>No public dataset test runs found.</Paragraph>}
      {runs?.length > 0 && <>
        <HStack wrap gap={2}>{runs.map((run) => <Button key={run.id} size="sm" variant={chosen === run.id ? 'solid' : 'outline'} onPress={() => { setChosen(run.id); setDetails(null) }}>{run.model} · {run.attempted} attempted</Button>)}</HStack>
        {details && <>
          {details.diagnostic && <Paragraph tone="warning">Provider check: HTTP {details.diagnostic.http_status} — {details.diagnostic.message}</Paragraph>}
          <Grid.Root columns={{ initial: 2, md: 4 }} gap={3}>{Object.entries(details.counts).map(([name, count]) => <Metric key={name} label={name} value={count} tone={statusTone(name)} />)}</Grid.Root>
          <Tabs.Root value={filter} onValueChange={setFilter} variant="line">
            <Tabs.List>{['ALL', 'VALID', 'REVIEW_REQUIRED', 'FAILED', 'SKIPPED'].map((status) => <Tabs.Trigger value={status} key={status}>{status.replace('_', ' ')}</Tabs.Trigger>)}<Tabs.Indicator /></Tabs.List>
            {['ALL', 'VALID', 'REVIEW_REQUIRED', 'FAILED', 'SKIPPED'].map((status) => <Tabs.Content key={status} value={status} inset="none">
              <Table.Container><Table.Root variant="line" size="sm" style={{ minInlineSize: '800px' }}>
                <Table.Caption>{status === 'ALL' ? 'All' : status} public test results, first 500 records</Table.Caption>
                <Table.Header><Table.Row>{['Source', 'Split', 'Status', 'Invoice', 'Vendor', 'Reference correct', 'Review comments'].map((name) => <Table.Head key={name}>{name}</Table.Head>)}</Table.Row></Table.Header>
                <Table.Body>{rows.map((row, index) => <Table.Row key={`${row.source_file}-${index}`}>
                  <Table.Head scope="row">{row.source_file}</Table.Head><Table.Cell>{row.split}</Table.Cell>
                  <Table.Cell><Badge tone={statusTone(row.validation_status)}>{row.validation_status}</Badge></Table.Cell>
                  <Table.Cell>{display(row.invoice_number)}</Table.Cell><Table.Cell>{display(row.vendor_name)}</Table.Cell>
                  <Table.Cell>{row.reference_fields_correct}/{row.reference_fields_evaluated}</Table.Cell><Table.Cell>{display(row.review_comments)}</Table.Cell>
                </Table.Row>)}</Table.Body>
              </Table.Root></Table.Container>
            </Tabs.Content>)}
          </Tabs.Root>
          <HStack wrap gap={2}>{['Test_Results.xlsx', 'Invoice_Results.csv', 'Test_Results.zip'].map((name) => <DownloadButton key={name} path={`/tests/${chosen}/${name}`}>{name}</DownloadButton>)}</HStack>
        </>}
      </>}
    </VStack></Collapsible.ContentInner></Collapsible.Content>
  </Collapsible.Root>
}

export default function App() {
  const [config, setConfig] = useState(null)
  const [history, setHistory] = useState(null)
  const [batch, setBatch] = useState(null)
  const [files, setFiles] = useState([])
  const [rejected, setRejected] = useState([])
  const [threshold, setThreshold] = useState(95)
  const [tolerance, setTolerance] = useState(0.02)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [runWarnings, setRunWarnings] = useState([])
  const [resetStep, setResetStep] = useState(0)
  const [resetToken, setResetToken] = useState(null)
  const [resetBusy, setResetBusy] = useState(false)
  const refreshConfig = async () => {
    const data = await api('/config')
    setConfig((previous) => {
      if (previous?.generation && previous.generation !== data.generation) setBatch(null)
      return data
    })
    return data
  }
  const refreshHistory = async () => setHistory(await api('/history'))
  useEffect(() => {
    refreshConfig().then((data) => { setThreshold(data.minimum_field_confidence); setTolerance(data.default_tolerance) }).catch((err) => setError(err.message))
  }, [])
  const processFiles = async () => {
    if (!files.length) return
    setBusy(true); setError(''); setMessage(''); setRunWarnings([])
    try {
      const body = new FormData()
      files.forEach((file) => body.append('files', file))
      body.append('tolerance', String(tolerance))
      body.append('threshold', String(threshold))
      const data = await api('/process', { method: 'POST', body })
      setBatch(data); setFiles([]); setHistory(null)
      setRunWarnings(data.warnings || [])
      setMessage(`Saved ${data.counts.total} invoice results to local history; review OCR-only and imported rows.`)
      await refreshConfig()
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }
  const runOutlook = async () => {
    setBusy(true); setError(''); setMessage(''); setRunWarnings([])
    try {
      const data = await api('/outlook/run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tolerance, threshold }) })
      if (data.batch_id) setBatch(data)
      setRunWarnings(data.warnings || [])
      setHistory(null)
      setMessage(data.batch_id
        ? `Saved ${data.counts.total} results from ${data.emails_processed} new emails; ${data.counts.failed} failed, ${data.counts.review} need review. ${data.emails_already_processed} emails already processed. ${data.ignored_files} other files or subfolders ignored.`
        : data.emails_already_processed === 0 && data.ignored_files === 0 && !(data.warnings || []).length
          ? 'The Outlook folder is empty. Save .msg or .eml email files in the folder shown below, then select Run again.'
          : `No new emails processed. ${data.emails_already_processed} already processed; ${data.ignored_files} other files or subfolders ignored.`)
      await refreshConfig()
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }
  const openBatch = async (id) => {
    try { setBatch(await api(`/batches/${id}`)); setMessage('Saved batch loaded.'); setError(''); window.scrollTo({ top: 0, behavior: 'smooth' }) }
    catch (err) { setError(err.message) }
  }
  const beginReset = async () => {
    try { setResetToken(await api('/reset/prepare', { method: 'POST' })); setResetStep(2) }
    catch (err) { setError(err.message); setResetStep(0) }
  }
  const confirmReset = async (event) => {
    event.preventDefault()
    setResetBusy(true)
    try {
      const data = await api('/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken.token, generation: resetToken.generation, confirmation: 'CLEAR SAVED INVOICES' }) })
      setBatch(null); setHistory(null); setFiles([]); setResetStep(0); setResetToken(null); setRunWarnings([])
      setMessage(`Reset complete: ${data.cleared_records} invoice rows cleared and ${data.deleted_files} uploaded files removed. Trained models and ${data.retained_training_examples} reviewed examples were kept.`); setError('')
      await refreshConfig()
    } catch (err) { setError(err.message); setResetStep(0) }
    finally { setResetBusy(false) }
  }
  return <>
    <AppBar.Root variant="surface" bordered>
      <Container measure="max"><AppBar.Toolbar inset="none">
        <AppBar.Start><HStack align="center" gap={2}><Badge tone="accent">AI</Badge><Text as="span" variant="title-sm" weight="semibold">Invoice Intelligence</Text></HStack></AppBar.Start>
        <AppBar.End><HStack align="center" gap={2}><Badge tone={config?.intake_ready ? 'warning' : 'danger'}>{config?.intake_ready ? 'Upload ready · review required' : 'Storage unavailable'}</Badge><Button size="sm" variant="ghost" tone="danger" onPress={() => setResetStep(1)}>Reset data</Button></HStack></AppBar.End>
      </AppBar.Toolbar></Container>
    </AppBar.Root>
    <main>
      <Section spacing="lg" aria-labelledby="page-heading"><Container measure="max"><VStack gap={5}>
        <VStack gap={2}><Eyebrow>Document operations</Eyebrow><Heading id="page-heading" level={1} variant="display-sm">Invoice processing, without the busywork.</Heading>
          <Paragraph variant="body-lg" tone="secondary">Upload PDFs, images, ZIPs, or Outlook emails. Your own Python extractor reads the invoice. Corrections retrain its field model locally; every draft still needs verification.</Paragraph></VStack>
        {(error || message || !config?.api_ready || !config?.storage_ready) && <Card.Root variant="subtle" size="sm"><Card.Content><VStack gap={1} aria-live="polite">
          {error && <Paragraph tone="danger">{error}</Paragraph>}
          {message && <Paragraph tone="success">{message}</Paragraph>}
          {runWarnings.map((warning, index) => <Paragraph key={index} tone="warning">{warning}</Paragraph>)}
          {config && !config.api_ready && <Paragraph tone="warning">{config.setup_message || 'Automatic extraction is unavailable.'}</Paragraph>}
          {config && !config.storage_ready && <Paragraph tone="danger">Local storage is unavailable. Check LOCAL_STORAGE_DIR and folder permissions.</Paragraph>}
        </VStack></Card.Content></Card.Root>}
        <Grid.Root columns={{ initial: 1, lg: 2 }} gap={4}>
          <Card.Root variant="elevated" size="lg">
            <Card.Header><Card.Title as="h2">Upload and process</Card.Title><Card.Description>PDFs, images, ZIP archives, Outlook .msg or .eml files, CSV and XLSX spreadsheets. Each document or spreadsheet row gets its own result.</Card.Description></Card.Header>
            <Card.Content><VStack gap={4}>
              <Field.Root><Field.Label>Invoice source files</Field.Label>
                <FileUpload.Root files={files} onFilesChange={setFiles} onRejectedFilesChange={setRejected} multiple appendFiles accept=".pdf,.png,.jpg,.jpeg,.tiff,.webp,.bmp,.zip,.msg,.eml,.csv,.xlsx" maxFiles={100} maxSize={(config?.max_file_mb || 30) * 1000000}>
                  <FileUpload.HiddenInput />
                  <FileUpload.Dropzone><VStack align="center" gap={2}>
                    <Text as="span" variant="title-sm">Drop invoice files here</Text>
                    <Paragraph tone="secondary">Or browse to select files · Up to {config?.max_file_mb || 30} MB each</Paragraph>
                    <FileUpload.Trigger>Browse files</FileUpload.Trigger>
                  </VStack></FileUpload.Dropzone>
                  {files.length > 0 && <FileUpload.ItemGroup>{files.map((file, index) => <FileUpload.Item key={`${file.name}-${index}`} file={file}>
                    <FileUpload.ItemName /><FileUpload.ItemSize /><FileUpload.ItemDeleteTrigger aria-label={`Remove ${file.name}`} />
                  </FileUpload.Item>)}</FileUpload.ItemGroup>}
                </FileUpload.Root>
                <Field.Description>Outlook email body, signature and metadata are ignored. Every local draft and imported spreadsheet row requires manual review. CSV must be UTF-8; Excel must be XLSX.</Field.Description>
              </Field.Root>
              {rejected.map((item, index) => <Paragraph key={index} tone="danger">{item.file?.name || 'File'}: {item.reason || 'File type, count or size was rejected'}</Paragraph>)}
              {busy && <Progress.Root value={null}><Progress.Label>Processing invoices</Progress.Label><Progress.Track><Progress.Indicator /></Progress.Track></Progress.Root>}
            </VStack></Card.Content>
            <Card.Footer><Button size="lg" tone="accent" loading={busy} disabled={!files.length || !config?.intake_ready || busy} onPress={processFiles}>Process {files.length ? `${files.length} file${files.length === 1 ? '' : 's'}` : 'invoices'}</Button></Card.Footer>
          </Card.Root>
          <Card.Root variant="outline" size="lg">
            <Card.Header><Card.Title as="h2">Processing setup</Card.Title><Card.Description>Review threshold and rounding tolerance apply to the next batch.</Card.Description></Card.Header>
            <Card.Content><VStack gap={4}>
              <Field.Root><Field.Label>Human-review threshold (%)</Field.Label>
                <NumberInput.Root value={threshold} onValueChange={(value) => setThreshold(value ?? 95)} min={95} max={100} step={1}>
                  <NumberInput.Input name="threshold" /><NumberInput.Decrement aria-label="Decrease threshold" /><NumberInput.Increment aria-label="Increase threshold" />
                </NumberInput.Root><Field.Description>Every local draft needs review; this setting applies when a model provides calibrated field scores.</Field.Description>
              </Field.Root>
              <Field.Root><Field.Label>Rounding tolerance</Field.Label>
                <NumberInput.Root value={tolerance} onValueChange={(value) => setTolerance(value ?? 0)} min={0} max={100} step={0.01} precision={2}>
                  <NumberInput.Input name="tolerance" /><NumberInput.Decrement aria-label="Decrease tolerance" /><NumberInput.Increment aria-label="Increase tolerance" />
                </NumberInput.Root><Field.Description>Allowed amount difference during validation.</Field.Description>
              </Field.Root>
              <HStack wrap gap={2}><Badge tone="info">Extraction mode</Badge><Text as="span" variant="body-sm">{config?.model || 'Loading…'}</Text></HStack>
              <HStack wrap gap={2}><Badge tone="neutral">Local history</Badge><Text as="span" variant="body-sm">{config?.records ?? 0} records across {config?.batches ?? 0} batches</Text></HStack>
              <HStack wrap gap={2}><Badge tone="info">Reviewed examples</Badge><Text as="span" variant="body-sm">{config?.reviewed_training_examples ?? 0} used for local retraining</Text></HStack>
              <Paragraph variant="body-sm" tone="muted">Upload → local OCR/model draft → manual review → CSV / Excel</Paragraph>
            </VStack></Card.Content>
          </Card.Root>
        </Grid.Root>
        <Card.Root variant="outline" size="lg">
            <Card.Header><Card.Title as="h2">Outlook folder</Card.Title><Card.Description>Save Outlook .msg or .eml email files in this local folder, then select Run. Only actual supported attachments enter the invoice pipeline.</Card.Description></Card.Header>
          <Card.Content><VStack gap={2}>
            <Text as="span" variant="body-sm" weight="semibold">Folder on this computer</Text>
            <Text as="span" variant="body-sm">{config?.outlook_folder || 'Loading folder…'}</Text>
            <Paragraph tone="secondary">Already processed emails are skipped on later runs. New invoice results append to the cumulative Invoice_data.csv alongside manual uploads; original emails stay in Outlook.</Paragraph>
          </VStack></Card.Content>
          <Card.Footer><HStack wrap gap={2}>
            <Button size="lg" tone="accent" loading={busy} disabled={busy || !config?.intake_ready} onPress={runOutlook}>Run</Button>
            {(config?.records ?? 0) > 0 && <DownloadButton path="/history/MDM/Invoice_data.csv">Download cumulative Invoice_data.csv</DownloadButton>}
          </HStack></Card.Footer>
        </Card.Root>
      </VStack></Container></Section>
      <Results batch={batch} columns={config?.mdm_columns || []} requiredFields={config?.required_fields || []} tolerance={tolerance} threshold={threshold} onSaved={(data) => { setBatch(data); setHistory(null); setMessage(data.model_retrained ? 'Correction saved and your field model retrained.' : data.training_example_saved ? 'Correction saved; no readable field labels were available to train.' : 'Correction saved; no source document was available for training.'); setRunWarnings(data.warnings || []); refreshConfig().catch((err) => setError(err.message)) }} onError={setError} />
      <Section spacing="md" aria-label="Reports and history"><Container measure="max"><VStack gap={3}>
        <History history={history} onRefresh={refreshHistory} onOpen={openBatch} onError={setError} />
        <Benchmarks onError={setError} />
      </VStack></Container></Section>
    </main>
    <AlertDialog.Root open={resetStep === 1} onOpenChange={(open) => !open && setResetStep(0)}>
      <AlertDialog.Portal><AlertDialog.Overlay /><AlertDialog.Content>
        <AlertDialog.Header><AlertDialog.Title>Are you sure?</AlertDialog.Title><AlertDialog.Description>Reset will clear saved invoice rows and uploaded files. Your trained models and reviewed training examples will stay. Continue to the final confirmation?</AlertDialog.Description></AlertDialog.Header>
        <AlertDialog.Footer><AlertDialog.Cancel asChild><Button variant="outline" onPress={() => setResetStep(0)}>No, keep data</Button></AlertDialog.Cancel>
          <AlertDialog.Action asChild><Button tone="warning" onPress={beginReset}>Yes, continue</Button></AlertDialog.Action></AlertDialog.Footer>
      </AlertDialog.Content></AlertDialog.Portal>
    </AlertDialog.Root>
    <AlertDialog.Root open={resetStep === 2} onOpenChange={(open) => !open && setResetStep(0)}>
      <AlertDialog.Portal><AlertDialog.Overlay /><AlertDialog.Content>
        <AlertDialog.Header><AlertDialog.Title>Clearing invoices — final confirmation</AlertDialog.Title><AlertDialog.Description>Step 2 of 2: Remove uploaded invoices, Outlook email files and saved invoice rows? CSV and Excel reports will keep their headers but contain no invoice rows. Trained models and reviewed examples remain.</AlertDialog.Description></AlertDialog.Header>
        <AlertDialog.Body><Paragraph tone="secondary">This cannot be undone in the app. Close Excel and other running copies first. Code, API configuration and files outside invoice intake are not deleted.</Paragraph></AlertDialog.Body>
        <AlertDialog.Footer><AlertDialog.Cancel asChild><Button variant="outline" onPress={() => setResetStep(0)}>No, cancel</Button></AlertDialog.Cancel>
          <AlertDialog.Action asChild><Button tone="danger" loading={resetBusy} onClick={confirmReset}>Yes, clear invoice rows</Button></AlertDialog.Action></AlertDialog.Footer>
      </AlertDialog.Content></AlertDialog.Portal>
    </AlertDialog.Root>
  </>
}

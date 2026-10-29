import { useState } from 'react';
import { useApp } from '../context/AppContext.jsx';
import { Page, Card, Field, Table, row, BTN, GHOST, INPUT } from '../components/ui.jsx';

export function ClubEvents() {
  const { user, events, setEvents, log, toast } = useApp();
  const blank = { name: '', date: '', venue: '', url: '', pts: '' };
  const [f, setF] = useState(blank);
  const [error, setError] = useState('');
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = (e) => {
    e.preventDefault();
    if (!f.name || !f.date || !f.venue || !(+f.pts > 0)) return setError('Title, date, venue and points are required.');
    setEvents((v) => [...v, { id: 100 + v.length, club: user.club, ...f, pts: +f.pts }]);
    log('PUBLISH_EVENT', f.name); toast('Event published'); setF(blank); setError('');
  };
  return (
    <Page title={`${user.club} — Events`} sub="Publish events with standard points.">
      <Card title="Publish event">
        <form onSubmit={submit} className="grid md:grid-cols-3 gap-3">
          <Field label="Title"><input className={INPUT} value={f.name} onChange={set('name')} /></Field>
          <Field label="Date"><input type="date" className={INPUT} value={f.date} onChange={set('date')} /></Field>
          <Field label="Venue"><input className={INPUT} value={f.venue} onChange={set('venue')} /></Field>
          <Field label="Registration URL"><input className={INPUT} value={f.url} onChange={set('url')} /></Field>
          <Field label="Standard points"><input type="number" className={INPUT} value={f.pts} onChange={set('pts')} /></Field>
          <div className="flex items-end"><button className={BTN} type="submit">Publish event</button></div>
          {error && <p className="md:col-span-3 text-brandl text-sm">{error}</p>}
        </form>
      </Card>
      <Card title="Published events">
        <Table cols={['Event', 'Date', 'Venue', 'Registration', 'Points']} rows={events.filter((e) => e.club === user.club).map((e) => row(e.id, [e.name, e.date, e.venue, e.url || '—', e.pts]))} />
      </Card>
    </Page>
  );
}

export function ClubQueue() {
  const { user, requests, setRequests, events, eventName, log, toast } = useApp();
  const [comments, setComments] = useState({});
  const queue = requests.filter((r) => r.st === 'PENDING_CLUB' && events.find((e) => e.id === r.ev)?.club === user.club);
  const act = (r, st, fallback) => {
    setRequests((x) => x.map((y) => (y.id === r.id ? { ...y, st, cm: comments[r.id] || fallback } : y)));
    log(st === 'REJECTED' ? 'CLUB_REJECT' : 'CLUB_VERIFY', `REQUEST #${r.id}`);
    toast(st === 'REJECTED' ? 'Claim rejected' : 'Forwarded to Dean');
  };
  return (
    <Page title="Verification Queue" sub="Verify claims for your events, then forward to the Dean. Rejections need a comment.">
      <Card>
        <Table cols={['ID', 'Student', 'Event', 'Type', 'Pts', 'Reviewer comment', 'Action']} empty="Queue is clear."
          rows={queue.map((r) => row(r.id, [`#${r.id}`, r.usn, eventName(r.ev), r.type, r.pts,
            <input className={INPUT} placeholder="Comment" value={comments[r.id] || ''} onChange={(e) => setComments({ ...comments, [r.id]: e.target.value })} />,
            <div className="flex gap-1">
              <button className={BTN} onClick={() => act(r, 'PENDING_DEAN', 'Verified by club.')}>Verify & forward</button>
              <button className={GHOST} disabled={!(comments[r.id] || '').trim()} onClick={() => act(r, 'REJECTED')}>Reject</button>
            </div>]))} />
      </Card>
    </Page>
  );
}

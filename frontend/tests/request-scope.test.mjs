import assert from 'node:assert/strict';
import test from 'node:test';
import {RequestScope, StaleRequestError} from '../src/request-scope.ts';

function delayed() {
  let resolve;
  const promise = new Promise(done => {resolve=done;});
  return {promise,resolve};
}

test('logout aborts pending reads and buffered former-user data cannot overwrite the next user', async () => {
  const scope = new RequestScope();
  const ticket = scope.begin();
  const reply = delayed();
  let displayed = 'B current loans';
  // Deliberately ignore abort, as a buffered or mocked response can still settle.
  const oldRead = (async () => {const data = await reply.promise; ticket.check(); displayed=data;})();
  scope.invalidate();
  assert.equal(ticket.signal.aborted,true);
  const newTicket = scope.begin();
  reply.resolve('A private loans');
  await assert.rejects(oldRead,StaleRequestError);
  assert.equal(displayed,'B current loans');
  assert.equal(newTicket.current(),true);
});

test('late 401 cannot expire a new session and old finally cannot clear its loading state', async () => {
  const scope = new RequestScope();
  const oldVersion = scope.version, ticket = scope.begin(), reply = delayed();
  let user = 'A', loading = true;
  const pending = (async () => {
    try {const status = await reply.promise; ticket.check(); if (status===401) user=null;}
    finally {if (scope.isCurrent(oldVersion)) loading=false; ticket.release();}
  })();
  scope.invalidate(); user='B';
  reply.resolve(401);
  await assert.rejects(pending,StaleRequestError);
  assert.equal(user,'B'); assert.equal(loading,true);
});

test('current responses are applied and completed requests are released from cancellation', async () => {
  const scope = new RequestScope();
  const ticket = scope.begin();
  const response = await Promise.resolve({user:'A'});
  ticket.check(); assert.equal(response.user,'A');
  ticket.release(); scope.invalidate();
  assert.equal(ticket.signal.aborted,false);
  assert.equal(ticket.current(),false);
});

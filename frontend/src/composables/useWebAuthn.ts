/* WebAuthn helpers — base64url<->ArrayBuffer marshalling and the two
 * navigator.credentials calls. The backend speaks PublicKeyCredential
 * options in the JSON shape the spec defines (challenge + ids are
 * base64url strings); the browser API expects the same fields as
 * ArrayBuffers. We bridge here. */

function b64urlToBuf(b64url: string): ArrayBuffer {
  const pad = '='.repeat((4 - (b64url.length % 4)) % 4)
  const b64 = (b64url + pad).replace(/-/g, '+').replace(/_/g, '/')
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes.buffer
}

function bufToB64url(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf)
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export function isWebAuthnSupported(): boolean {
  return typeof window !== 'undefined' && !!window.PublicKeyCredential
}

interface RegisterOptions {
  challenge: string
  rp: { id: string; name: string }
  user: { id: string; name: string; displayName: string }
  pubKeyCredParams: { alg: number; type: 'public-key' }[]
  excludeCredentials?: { id: string; type: 'public-key'; transports?: string[] }[]
  authenticatorSelection?: {
    userVerification?: 'required' | 'preferred' | 'discouraged'
    residentKey?: 'required' | 'preferred' | 'discouraged'
  }
  attestation?: 'none' | 'indirect' | 'direct'
  timeout?: number
}

interface AuthOptions {
  challenge: string
  rpId: string
  allowCredentials?: { id: string; type: 'public-key'; transports?: string[] }[]
  userVerification?: 'required' | 'preferred' | 'discouraged'
  timeout?: number
}

export async function performRegistration(serverOptions: RegisterOptions) {
  const publicKey: PublicKeyCredentialCreationOptions = {
    challenge: b64urlToBuf(serverOptions.challenge),
    rp: serverOptions.rp,
    user: {
      id: b64urlToBuf(serverOptions.user.id),
      name: serverOptions.user.name,
      displayName: serverOptions.user.displayName,
    },
    pubKeyCredParams: serverOptions.pubKeyCredParams,
    excludeCredentials: serverOptions.excludeCredentials?.map((c) => ({
      id: b64urlToBuf(c.id),
      type: c.type,
      transports: c.transports as AuthenticatorTransport[] | undefined,
    })),
    authenticatorSelection: serverOptions.authenticatorSelection,
    attestation: serverOptions.attestation,
    timeout: serverOptions.timeout ?? 60000,
  }

  const cred = (await navigator.credentials.create({
    publicKey,
  })) as PublicKeyCredential | null
  if (!cred) throw new Error('No credential returned')
  const r = cred.response as AuthenticatorAttestationResponse

  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      attestationObject: bufToB64url(r.attestationObject),
      clientDataJSON: bufToB64url(r.clientDataJSON),
      transports: (r as AuthenticatorAttestationResponse & { getTransports?(): string[] })
        .getTransports?.() ?? [],
    },
    clientExtensionResults: cred.getClientExtensionResults(),
  }
}

export async function performAuthentication(serverOptions: AuthOptions) {
  const publicKey: PublicKeyCredentialRequestOptions = {
    challenge: b64urlToBuf(serverOptions.challenge),
    rpId: serverOptions.rpId,
    allowCredentials: serverOptions.allowCredentials?.map((c) => ({
      id: b64urlToBuf(c.id),
      type: c.type,
      transports: c.transports as AuthenticatorTransport[] | undefined,
    })),
    userVerification: serverOptions.userVerification,
    timeout: serverOptions.timeout ?? 60000,
  }

  const cred = (await navigator.credentials.get({
    publicKey,
  })) as PublicKeyCredential | null
  if (!cred) throw new Error('No credential returned')
  const r = cred.response as AuthenticatorAssertionResponse

  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      authenticatorData: bufToB64url(r.authenticatorData),
      clientDataJSON: bufToB64url(r.clientDataJSON),
      signature: bufToB64url(r.signature),
      userHandle: r.userHandle ? bufToB64url(r.userHandle) : null,
    },
    clientExtensionResults: cred.getClientExtensionResults(),
  }
}

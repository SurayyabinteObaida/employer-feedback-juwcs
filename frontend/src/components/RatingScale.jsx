const SCALE = [5, 4, 3, 2, 1]

export default function RatingScale({ label, value, onChange }) {
  return (
    <div className="rating-row">
      <p>{label}</p>
      <div className="rating-options">
        {SCALE.map((n) => (
          <label key={n} className="rating-option">
            <input type="radio" checked={value === n} onChange={() => onChange(n)} required />
            {n}
          </label>
        ))}
      </div>
    </div>
  )
}

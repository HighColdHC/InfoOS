export function getStatusExplanation(status: string, blockKind: string): string {
  switch (blockKind) {
    case "repost_original":
      switch (status) {
        case "blocked":
          return "Repost content could not be fetched from X";
        case "failed":
          return "Repost processing failed — please retry";
        case "not_found":
          return "Repost not found — original post may have been deleted";
        default:
          return "Repost content not available";
      }
    case "quote":
      switch (status) {
        case "blocked":
          return "Quoted tweet could not be fetched";
        case "failed":
          return "Quote processing failed — please retry";
        case "not_found":
          return "Quoted tweet not found — may have been deleted";
        default:
          return "Quoted tweet content not available";
      }
    case "linked_article":
      switch (status) {
        case "blocked":
        case "failed":
          return "Article content could not be fetched";
        case "unsupported":
          return "Linked page type not supported for extraction";
        case "not_found":
          return "Article not found — page may have been removed";
        default:
          return "Article content not available";
      }
    case "image":
      switch (status) {
        case "blocked":
          return "Image blocked — may violate content policy";
        case "failed":
          return "Image processing failed — please retry";
        case "cleaned":
          return "Image cleaned from local storage";
        case "not_found":
          return "Image not found — may have been deleted";
        case "unsupported":
          return "Image format not supported";
        default:
          return "Image not available";
      }
    case "video":
      switch (status) {
        case "blocked":
          return "Video blocked — may violate content policy";
        case "failed":
          return "Video processing failed — please retry";
        case "cleaned":
          return "Video cleaned from local storage";
        case "not_found":
          return "Video not found — may have been deleted";
        case "unsupported":
          return "Video format not supported";
        default:
          return "Video not available";
      }
    default:
      switch (status) {
        case "blocked":
          return "Content blocked — may violate content policy";
        case "failed":
          return "Content processing failed — please retry";
        case "cleaned":
          return "Content cleaned from local storage";
        case "not_found":
          return "Content not found — may have been deleted";
        case "unsupported":
          return "Content format not supported";
        default:
          return "Content not available";
      }
  }
}
